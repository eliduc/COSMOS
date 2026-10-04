# [DRAFT — recommendation: DO NOT file, see investigation report] ArrayIndexOutOfBoundsException with impossible negative index in AbstractIfExistsNode.doRetractCounter

> **Investigation verdict (2026-07-09).** The follow-up evidence (two
> EXCEPTION_ACCESS_VIOLATION JVM crashes in *unrelated* subsystems — the
> bytecode interpreter and a G1 GC worker thread — in two independent
> processes within the same minute, on the same machine, under the same
> workload) demonstrates system-level memory corruption on this host, not a
> Timefold defect. A direct userspace memory test (write pattern / wait 2 s /
> verify, 64 GiB) subsequently confirmed it: 13 corrupted words in 45 minutes,
> always in bits 32-47 of a 64-bit word, three times as pairs 16 bytes apart
> within one cache line - a memory-controller/bus-level fault signature.
> Filing this against timefold-solver would be noise.
> This draft is retained in case the identical signature ever reappears on a
> machine that has passed a memory test — then it becomes filable, with the
> package below.

## Environment

- Timefold Solver **2.0.0** Community Edition (Bavet), via `timefold-solver-quarkus` (Quarkus 3.34.5)
- JVM: Eclipse Temurin **21.0.11+10** (OpenJDK 64-Bit Server VM, G1 GC, `-Xms512m -Xmx8g`)
- OS: Windows 11 Home build 26100, AMD Ryzen AI MAX+ 395, 32 logical CPUs, 128 GB RAM
- Solver: single-threaded (`moveThreadCount` unset), `environmentMode=NO_ASSERT`, fixed `randomSeed=7`
- Phases: default Construction Heuristic + default Local Search (Late Acceptance, size 400)

## What happened (single occurrence, 2026-07-07 11:00:25 local)

During a synchronous single-solver run on a 5,613-entity flight-crew instance
(one `@PlanningVariable` per entity), the solver threw:

```
java.lang.ArrayIndexOutOfBoundsException: Index -1071208737 out of bounds for length 6
    at ai.timefold.solver.core.impl.bavet.common.AbstractIfExistsNode.doRetractCounter(AbstractIfExistsNode.java:179)
    at ai.timefold.solver.core.impl.bavet.common.AbstractIfExistsNode.incrementCounterRight(AbstractIfExistsNode.java:108)
    at ai.timefold.solver.core.impl.bavet.common.AbstractIfExistsNode.updateCounterFromRight(AbstractIfExistsNode.java:164)
    at ai.timefold.solver.core.impl.bavet.common.AbstractIndexedIfExistsNode.lambda$updateCounterLeft$0(AbstractIndexedIfExistsNode.java:154)
    at ai.timefold.solver.core.impl.util.ElementAwareLinkedList.forEach(ElementAwareLinkedList.java:149)
    at ai.timefold.solver.core.impl.bavet.common.index.LinkedListIndexerBackend.forEach(LinkedListIndexerBackend.java:40)
    at ai.timefold.solver.core.impl.bavet.common.index.EqualIndexer.forEach(EqualIndexer.java:104)
    at ai.timefold.solver.core.impl.bavet.common.AbstractIndexedIfExistsNode.updateCounterLeft(AbstractIndexedIfExistsNode.java:154)
    at ai.timefold.solver.core.impl.bavet.common.AbstractIndexedIfExistsNode.updateRight(AbstractIndexedIfExistsNode.java:184)
    at ai.timefold.solver.core.impl.bavet.common.AbstractIndexedIfExistsNode.updateRight(AbstractIndexedIfExistsNode.java:23)
    at ai.timefold.solver.core.impl.bavet.common.tuple.RightTupleLifecycleImpl.update(RightTupleLifecycleImpl.java:20)
    at ai.timefold.solver.core.impl.bavet.common.tuple.AggregatedTupleLifecycle.update(AggregatedTupleLifecycle.java:25)
```

The crash happened ~313 s into the solve — within seconds of the Construction
Heuristic → Local Search phase transition (CH takes 316 s on this instance on
that machine under that day's load).

## Constraint network shape at the crash site

Two *structurally identical* filtering `ifNotExists` subtrees over the same
entity class (self-join "adjacent pair with no other assignment of the same
employee in between"): one shared by two constraints via a helper whose
non-capturing lambdas are singletons (node-shared), one with separate lambda
instances (not node-shared). So the network holds two independent
`AbstractIndexedIfExistsNode(isFiltering=true)` instances over ~5,613
entities of `FlightAssignment`.

```java
cf.forEach(FlightAssignment.class)
  .filter(fa -> fa.getEmployee() != null)
  .join(FlightAssignment.class,
        Joiners.equal(FlightAssignment::getEmployee),
        Joiners.lessThan(FlightAssignment::getDepartureUTCDateTime),
        Joiners.filtering((fa1, fa2) -> !fa1.getId().equals(fa2.getId())))
  .ifNotExists(FlightAssignment.class,
        Joiners.equal((fa1, fa2) -> fa1.getEmployee(), FlightAssignment::getEmployee),
        Joiners.filtering((fa1, fa2, other) -> /* other strictly between fa1 and fa2 */))
```

## Why this is not a Timefold logic bug

1. **The failing line is an enum switch.** `AbstractIfExistsNode.java:179` is
   `switch (counter.state)`; javac compiles it to
   `$SwitchMap$...$TupleState[counter.state.ordinal()]` — an `iaload` on a
   6-element array (`TupleState` has 6 constants — the "length 6"). For any
   valid `TupleState` reference `ordinal()` returns 0–5. Index
   **-1071208737** cannot be produced by this bytecode under JVM-spec
   semantics regardless of any state-machine invariant violation. (A genuine
   invariant violation surfaces as the `IllegalStateException("Impossible
   state ...")` in the `default` branch.)
2. **A byte-identical deterministic computation both passed and failed.**
   Same payload, same JAR, same JVM build, same seed, fresh JVM per run,
   single-threaded solver. A 600 s run (config-identical for the first 600 s)
   completed cleanly 11 minutes before the 1800 s run crashed at ~313 s. The
   seed-independent Construction Heuristic passed cleanly in ≥6 other runs of
   the same payload (and, later, in 34 more mirror runs — see below).
3. **No incremental-score corruption is detectable.**
   - `FULL_ASSERT`: full 3-constraint profile and every pairwise/single
     subset, on 60/120/300-entity subsets of the real instance, multiple
     seeds, ~30–75 min each — all clean.
   - `FULL_ASSERT`: self-contained synthetic model with the same two
     ifNotExists shapes (120–300 entities) — all clean.
   - `STEP_ASSERT`: the full 5,613-entity instance, full Construction
     Heuristic + Local Search — clean.
4. **Mirror-environment reproduction farm: 36 attempts, 0 recurrences of the
   AIOOBE — but 2 JVM hard crashes.** Fresh JVM per attempt, identical jar,
   flags, HTTP request, payload; seeds 7/7/23; 480 s budget covering the
   original crash window with margin.
   - 34 attempts: clean, with bit-identical CH scores (0hard/547172soft,
     5,613 steps) across all attempts and seeds.
   - Attempt \#3 on port 9093: **EXCEPTION_ACCESS_VIOLATION** at 220 s, in the
     *bytecode interpreter* executing `java.lang.String.valueOf` (hs_err:
     reading 0x0b039d08, far outside the [0x600000000, 0x800000000) heap —
     a garbage object reference).
   - Attempt \#3 on port 9094 (independent process): **EXCEPTION_ACCESS_VIOLATION**
     102 s later, in *G1 "GC Thread#10"* while walking the object graph
     (reading 0x0000081eccd4e366).
   - Two unrelated JVM subsystems, two independent processes, same minute,
     then 70+ minutes of further attempts fully clean. That correlation is
     only explainable by a host-level cause (DRAM/SoC/firmware/driver), not
     by any single-process software defect.
5. **Windows System event log:** no WHEA / Kernel-Power / BugCheck entries —
   i.e. no hardware-*detected* errors; consistent with silent corruption on
   a non-ECC-reporting client platform.
6. **Tracker check:** zero prior mentions of `doRetractCounter` in this
   tracker; the counter/propagation code path is unchanged from v2.0.0
   through v2.2.0 (only node-deactivation machinery was added).

## Repro package (would attach if ever filed)

- `ReproMain.java` — standalone runner, Java replicas of the Kotlin
  constraints (same stream shapes and node-sharing).
- `IfExistsAioobeReproducer.java` — self-contained synthetic model, no
  dataset needed.
- `farm.py` — mirror-environment reproduction farm (fresh JVM per attempt,
  warm-retry-on-500 diagnostics).
- The 5,613-entity JSON instance (2.2 MB).
- Both `hs_err_pid*.log` files.
