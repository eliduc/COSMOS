import ai.timefold.solver.core.api.domain.common.PlanningId;
import ai.timefold.solver.core.api.domain.entity.PlanningEntity;
import ai.timefold.solver.core.api.domain.solution.PlanningEntityCollectionProperty;
import ai.timefold.solver.core.api.domain.solution.PlanningScore;
import ai.timefold.solver.core.api.domain.solution.PlanningSolution;
import ai.timefold.solver.core.api.domain.solution.ProblemFactCollectionProperty;
import ai.timefold.solver.core.api.domain.valuerange.ValueRangeProvider;
import ai.timefold.solver.core.api.domain.variable.PlanningVariable;
import ai.timefold.solver.core.api.score.HardSoftScore;
import ai.timefold.solver.core.api.score.stream.Constraint;
import ai.timefold.solver.core.api.score.stream.ConstraintFactory;
import ai.timefold.solver.core.api.score.stream.ConstraintProvider;
import ai.timefold.solver.core.api.score.stream.Joiners;
import ai.timefold.solver.core.api.score.stream.bi.BiConstraintStream;
import ai.timefold.solver.core.api.solver.SolverFactory;
import ai.timefold.solver.core.config.score.director.ScoreDirectorFactoryConfig;
import ai.timefold.solver.core.config.solver.EnvironmentMode;
import ai.timefold.solver.core.config.solver.SolverConfig;
import ai.timefold.solver.core.config.solver.termination.TerminationConfig;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;

/**
 * Self-contained reproducer for an ArrayIndexOutOfBoundsException in
 * AbstractIfExistsNode.doRetractCounter / score corruption with two
 * structurally-identical filtering ifNotExists constraint subtrees.
 *
 * Domain: Shift(id, start, end) with a single planning variable `employee`.
 * Two constraints replicate the "adjacent pair with no other shift between"
 * pattern:
 *  - waiting:   penalize gap in [60, 600) between employee-adjacent shifts
 *  - compact:   reward 200-gap for gap in [60, 200), join additionally
 *               filtered to non-overlapping pairs
 *
 * Args: <PAIR|CHAIN|WAIT> <FULL_ASSERT|NO_ASSERT|...> <seed> <seconds>
 *       [shifts] [employees] [days]
 */
public class IfExistsAioobeReproducer {

    // ------------------------- domain -------------------------

    @PlanningEntity
    public static class Shift {
        @PlanningId
        private Integer id;
        private int start; // minutes
        private int end;   // minutes

        @PlanningVariable
        private String employee;

        public Shift() {
        }

        public Shift(int id, int start, int end) {
            this.id = id;
            this.start = start;
            this.end = end;
        }

        public Integer getId() {
            return id;
        }

        public Integer getStart() {
            return start;
        }

        public Integer getEnd() {
            return end;
        }

        public String getEmployee() {
            return employee;
        }

        public void setEmployee(String employee) {
            this.employee = employee;
        }

        @Override
        public String toString() {
            return "Shift" + id + "[" + start + "-" + end + "@" + employee + "]";
        }
    }

    @PlanningSolution
    public static class Schedule {
        @ProblemFactCollectionProperty
        @ValueRangeProvider
        private List<String> employees;

        @PlanningEntityCollectionProperty
        private List<Shift> shifts;

        @PlanningScore
        private HardSoftScore score;

        public Schedule() {
        }

        public Schedule(List<String> employees, List<Shift> shifts) {
            this.employees = employees;
            this.shifts = shifts;
        }

        public List<String> getEmployees() {
            return employees;
        }

        public List<Shift> getShifts() {
            return shifts;
        }

        public HardSoftScore getScore() {
            return score;
        }

        public void setScore(HardSoftScore score) {
            this.score = score;
        }
    }

    // ------------------------- constraints -------------------------

    static int gap(Shift a, Shift b) {
        return b.getStart() - a.getEnd();
    }

    /** forEach -> join(same employee, a.start < b.start) -> ifNotExists(other between). */
    static BiConstraintStream<Shift, Shift> adjacent(ConstraintFactory cf) {
        return cf.forEach(Shift.class)
                .filter(s -> s.getEmployee() != null)
                .join(Shift.class,
                        Joiners.equal(Shift::getEmployee),
                        Joiners.lessThan(Shift::getStart),
                        Joiners.filtering((a, b) -> !a.getId().equals(b.getId())))
                .ifNotExists(Shift.class,
                        Joiners.equal((a, b) -> a.getEmployee(), Shift::getEmployee),
                        Joiners.filtering((a, b, other) ->
                                !other.getId().equals(a.getId()) && !other.getId().equals(b.getId())
                                        && a.getStart() < other.getStart()
                                        && other.getStart() < b.getStart()));
    }

    static Constraint minSit(ConstraintFactory cf) {
        return adjacent(cf)
                .filter((a, b) -> gap(a, b) < 60)
                .penalize(HardSoftScore.ofHard(5), (a, b) -> Math.max(1, 60 - gap(a, b)))
                .asConstraint("Min sit");
    }

    static Constraint waiting(ConstraintFactory cf) {
        return adjacent(cf)
                .filter((a, b) -> {
                    int g = gap(a, b);
                    return g >= 60 && g < 600;
                })
                .penalize(HardSoftScore.ONE_SOFT, IfExistsAioobeReproducer::gap)
                .asConstraint("Waiting");
    }

    /** Structurally identical second ifNotExists subtree (separate lambda
     *  instances -> NOT node-shared with adjacent()). */
    static Constraint compact(ConstraintFactory cf) {
        return cf.forEach(Shift.class)
                .filter(s -> s.getEmployee() != null)
                .join(Shift.class,
                        Joiners.equal(Shift::getEmployee),
                        Joiners.lessThan(Shift::getStart),
                        Joiners.filtering((a, b) -> !a.getId().equals(b.getId())
                                && b.getStart() >= a.getEnd()))
                .ifNotExists(Shift.class,
                        Joiners.equal((a, b) -> a.getEmployee(), Shift::getEmployee),
                        Joiners.filtering((a, b, other) ->
                                !other.getId().equals(a.getId()) && !other.getId().equals(b.getId())
                                        && a.getStart() < other.getStart()
                                        && other.getStart() < b.getStart()))
                .filter((a, b) -> {
                    int g = gap(a, b);
                    return g >= 60 && g < 200;
                })
                .reward(HardSoftScore.ONE_SOFT, (a, b) -> 200 - gap(a, b))
                .asConstraint("Compactness");
    }

    public static class PairProvider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { waiting(cf), compact(cf) };
        }
    }

    public static class TrioProvider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { minSit(cf), waiting(cf), compact(cf) };
        }
    }

    public static class ChainProvider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { compact(cf) };
        }
    }

    public static class WaitProvider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { waiting(cf) };
        }
    }

    // ------------------------- main -------------------------

    public static void main(String[] args) {
        String which = args.length > 0 ? args[0].toUpperCase() : "PAIR";
        EnvironmentMode mode = EnvironmentMode.valueOf(
                args.length > 1 ? args[1].toUpperCase() : "FULL_ASSERT");
        long seed = args.length > 2 ? Long.parseLong(args[2]) : 7;
        long seconds = args.length > 3 ? Long.parseLong(args[3]) : 600;
        int nShifts = args.length > 4 ? Integer.parseInt(args[4]) : 120;
        int nEmployees = args.length > 5 ? Integer.parseInt(args[5]) : 12;
        int nDays = args.length > 6 ? Integer.parseInt(args[6]) : 10;

        Class<? extends ConstraintProvider> provider = switch (which) {
            case "PAIR" -> PairProvider.class;
            case "TRIO" -> TrioProvider.class;
            case "CHAIN" -> ChainProvider.class;
            case "WAIT" -> WaitProvider.class;
            default -> throw new IllegalArgumentException(which);
        };

        // Deterministic dataset: nShifts shifts over nDays days.
        Random rnd = new Random(12345);
        List<Shift> shifts = new ArrayList<>();
        for (int i = 0; i < nShifts; i++) {
            int day = rnd.nextInt(nDays);
            int startInDay = 5 * 60 + rnd.nextInt(16 * 60); // 05:00..21:00
            int start = day * 24 * 60 + startInDay;
            int durationMin = 60 + rnd.nextInt(240);
            shifts.add(new Shift(i, start, start + durationMin));
        }
        List<String> employees = new ArrayList<>();
        for (int i = 0; i < nEmployees; i++) {
            employees.add("E" + i);
        }
        Schedule problem = new Schedule(employees, shifts);

        System.out.printf("provider=%s mode=%s seed=%d limit=%ds shifts=%d employees=%d days=%d%n",
                which, mode, seed, seconds, nShifts, nEmployees, nDays);

        SolverConfig cfg = new SolverConfig()
                .withSolutionClass(Schedule.class)
                .withEntityClasses(Shift.class)
                .withEnvironmentMode(mode);
        cfg.setRandomSeed(seed);
        cfg.setScoreDirectorFactoryConfig(new ScoreDirectorFactoryConfig()
                .withConstraintProviderClass(provider));
        cfg.setTerminationConfig(new TerminationConfig()
                .withSpentLimit(Duration.ofSeconds(seconds)));

        var solver = SolverFactory.<Schedule>create(cfg).buildSolver();
        long t0 = System.currentTimeMillis();
        try {
            Schedule solved = solver.solve(problem);
            System.out.printf("DONE score=%s elapsed=%.1fs%n",
                    solved.getScore(), (System.currentTimeMillis() - t0) / 1000.0);
        } catch (Throwable t) {
            System.out.printf("CRASHED after %.1fs%n", (System.currentTimeMillis() - t0) / 1000.0);
            t.printStackTrace(System.out);
            System.exit(2);
        }
    }
}
