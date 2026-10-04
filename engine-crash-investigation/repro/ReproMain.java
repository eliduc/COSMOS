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
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.acme.flighcrewscheduling.domain.FlightAssignment;
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule;

import java.io.File;
import java.time.Duration;
import java.time.temporal.ChronoUnit;

/**
 * Standalone reproducer runner for the AbstractIfExistsNode.doRetractCounter
 * ArrayIndexOutOfBoundsException seen with the GERAD3 profile.
 *
 * Args: <payload.json> <CONSTRAINTS> <ENV_MODE> <seed> <seconds>
 *   CONSTRAINTS: FULL3 | G2 | PAIR_WAIT_CHAIN | PAIR_MINSIT_CHAIN | CHAIN
 *                | ADJ2 | WAIT1 | TRIO
 *   ENV_MODE:    FULL_ASSERT | STEP_ASSERT | NO_ASSERT | TRACKED_FULL_ASSERT ...
 */
public class ReproMain {

    public static void main(String[] args) throws Exception {
        String payload = args[0];
        String constraints = args[1].toUpperCase();
        EnvironmentMode mode = EnvironmentMode.valueOf(args[2].toUpperCase());
        long seed = Long.parseLong(args[3]);
        long seconds = Long.parseLong(args[4]);

        ObjectMapper om = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
        FlightCrewSchedule problem = om.readValue(new File(payload), FlightCrewSchedule.class);
        System.out.printf("payload=%s assignments=%d employees=%d constraints=%s mode=%s seed=%d limit=%ds%n",
                payload, problem.getFlightAssignments().size(), problem.getEmployees().size(),
                constraints, mode, seed, seconds);

        Class<? extends ConstraintProvider> providerClass = switch (constraints) {
            case "FULL3" -> org.acme.flighcrewscheduling.solver.GeradPhase3ConstraintProvider.class;
            case "G2" -> org.acme.flighcrewscheduling.solver.GeradPhase2ConstraintProvider.class;
            case "PAIR_WAIT_CHAIN" -> PairWaitChainProvider.class;
            case "PAIR_MINSIT_CHAIN" -> PairMinSitChainProvider.class;
            case "CHAIN" -> ChainOnlyProvider.class;
            case "ADJ2" -> Adj2Provider.class;
            case "WAIT1" -> Wait1Provider.class;
            case "TRIO" -> TrioProvider.class;
            default -> throw new IllegalArgumentException("unknown constraint set: " + constraints);
        };

        SolverConfig cfg = new SolverConfig()
                .withSolutionClass(FlightCrewSchedule.class)
                .withEntityClasses(FlightAssignment.class)
                .withEnvironmentMode(mode);
        cfg.setRandomSeed(seed);
        cfg.setScoreDirectorFactoryConfig(new ScoreDirectorFactoryConfig()
                .withConstraintProviderClass(providerClass));
        cfg.setTerminationConfig(new TerminationConfig()
                .withSpentLimit(Duration.ofSeconds(seconds)));

        var solver = SolverFactory.<FlightCrewSchedule>create(cfg).buildSolver();
        long t0 = System.currentTimeMillis();
        solver.addEventListener(e -> System.out.printf("[%7.1fs] new best: %s%n",
                (System.currentTimeMillis() - t0) / 1000.0, e.getNewBestScore()));
        try {
            FlightCrewSchedule solved = solver.solve(problem);
            System.out.printf("DONE score=%s elapsed=%.1fs%n",
                    solved.getScore(), (System.currentTimeMillis() - t0) / 1000.0);
        } catch (Throwable t) {
            System.out.printf("CRASHED after %.1fs%n", (System.currentTimeMillis() - t0) / 1000.0);
            t.printStackTrace(System.out);
            System.exit(2);
        }
    }

    // ------------------------------------------------------------------
    // Java replicas of the Kotlin constraints (same stream shapes, same
    // node-sharing behaviour: adjacent() is one call site -> its lambdas
    // are singletons -> the join+ifNotExists subtree is shared).
    // ------------------------------------------------------------------

    static int gapMin(FlightAssignment fa1, FlightAssignment fa2) {
        return (int) ChronoUnit.MINUTES.between(
                fa1.getFlight().getArrivalUTCDateTime(),
                fa2.getFlight().getDepartureUTCDateTime());
    }

    /** Replica of GeradConstraintProvider.adjacent(). */
    static BiConstraintStream<FlightAssignment, FlightAssignment> adjacent(ConstraintFactory cf) {
        return cf.forEach(FlightAssignment.class)
                .filter(fa -> fa.getEmployee() != null)
                .join(FlightAssignment.class,
                        Joiners.equal(FlightAssignment::getEmployee),
                        Joiners.lessThan(FlightAssignment::getDepartureUTCDateTime),
                        Joiners.filtering((fa1, fa2) -> !fa1.getId().equals(fa2.getId())))
                .ifNotExists(FlightAssignment.class,
                        Joiners.equal((fa1, fa2) -> fa1.getEmployee(), FlightAssignment::getEmployee),
                        Joiners.filtering((fa1, fa2, other) ->
                                !other.getId().equals(fa1.getId()) && !other.getId().equals(fa2.getId())
                                        && fa1.getDepartureUTCDateTime().isBefore(other.getDepartureUTCDateTime())
                                        && other.getDepartureUTCDateTime().isBefore(fa2.getDepartureUTCDateTime())));
    }

    /** Replica of GeradConstraintProvider.minSit. */
    static Constraint minSit(ConstraintFactory cf) {
        return adjacent(cf)
                .filter((fa1, fa2) -> gapMin(fa1, fa2) < 60)
                .penalize(HardSoftScore.ofHard(5), (fa1, fa2) -> Math.max(1, 60 - gapMin(fa1, fa2)))
                .asConstraint("G Min sit 60m");
    }

    /** Replica of GeradConstraintProvider.waitingCost. */
    static Constraint waiting(ConstraintFactory cf) {
        return adjacent(cf)
                .filter((fa1, fa2) -> {
                    int g = gapMin(fa1, fa2);
                    return g >= 60 && g < 600;
                })
                .penalize(HardSoftScore.ONE_SOFT, ReproMain::gapMin)
                .asConstraint("G Waiting");
    }

    /** Replica of GeradPhase3ConstraintProvider.chainCompactness. */
    static Constraint chainCompactness(ConstraintFactory cf) {
        return cf.forEach(FlightAssignment.class)
                .filter(fa -> fa.getEmployee() != null)
                .join(FlightAssignment.class,
                        Joiners.equal(FlightAssignment::getEmployee),
                        Joiners.lessThan(FlightAssignment::getDepartureUTCDateTime),
                        Joiners.filtering((fa1, fa2) -> !fa1.getId().equals(fa2.getId())
                                && !fa2.getFlight().getDepartureUTCDateTime()
                                        .isBefore(fa1.getFlight().getArrivalUTCDateTime())))
                .ifNotExists(FlightAssignment.class,
                        Joiners.equal((fa1, fa2) -> fa1.getEmployee(), FlightAssignment::getEmployee),
                        Joiners.filtering((fa1, fa2, other) ->
                                !other.getId().equals(fa1.getId()) && !other.getId().equals(fa2.getId())
                                        && fa1.getDepartureUTCDateTime().isBefore(other.getDepartureUTCDateTime())
                                        && other.getDepartureUTCDateTime().isBefore(fa2.getDepartureUTCDateTime())))
                .filter((fa1, fa2) -> {
                    int g = gapMin(fa1, fa2);
                    return g >= 60 && g < 200;
                })
                .reward(HardSoftScore.ONE_SOFT, (fa1, fa2) -> 200 - gapMin(fa1, fa2))
                .asConstraint("G3 Chain compactness");
    }

    public static class PairWaitChainProvider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { waiting(cf), chainCompactness(cf) };
        }
    }

    public static class PairMinSitChainProvider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { minSit(cf), chainCompactness(cf) };
        }
    }

    public static class ChainOnlyProvider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { chainCompactness(cf) };
        }
    }

    public static class Adj2Provider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { minSit(cf), waiting(cf) };
        }
    }

    public static class Wait1Provider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { waiting(cf) };
        }
    }

    public static class TrioProvider implements ConstraintProvider {
        @Override
        public Constraint[] defineConstraints(ConstraintFactory cf) {
            return new Constraint[] { minSit(cf), waiting(cf), chainCompactness(cf) };
        }
    }
}
