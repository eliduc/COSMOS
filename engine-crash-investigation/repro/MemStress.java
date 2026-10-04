import java.util.concurrent.atomic.AtomicLong;

/**
 * Userspace memory stress/verify tester.
 *
 * Each worker thread owns N long[] blocks. In a loop it fills each block with
 * a pattern derived from (blockId, pass, index) via a mixing function, sleeps
 * to let data sit in DRAM, then re-verifies every word. Any mismatch is a
 * genuine memory error (bit flip / corruption between write and read).
 *
 * Args: <totalGiB> <threads> <minutes>
 */
public class MemStress {

    static final AtomicLong errors = new AtomicLong();
    static final AtomicLong passes = new AtomicLong();

    static long mix(long blockId, long pass, long i) {
        long x = blockId * 0x9E3779B97F4A7C15L ^ pass * 0xC2B2AE3D27D4EB4FL ^ i;
        x ^= x >>> 33; x *= 0xFF51AFD7ED558CCDL;
        x ^= x >>> 33; x *= 0xC4CEB9FE1A85EC53L;
        x ^= x >>> 33;
        return x;
    }

    public static void main(String[] args) throws Exception {
        int totalGiB = args.length > 0 ? Integer.parseInt(args[0]) : 24;
        int nThreads = args.length > 1 ? Integer.parseInt(args[1]) : 8;
        long minutes = args.length > 2 ? Long.parseLong(args[2]) : 30;
        long deadline = System.currentTimeMillis() + minutes * 60_000;

        // 128 MiB blocks of long
        final int wordsPerBlock = 128 * 1024 * 1024 / 8;
        int totalBlocks = (int) (totalGiB * 8L); // 8 blocks per GiB
        int blocksPerThread = Math.max(1, totalBlocks / nThreads);

        System.out.printf("MemStress: %d GiB, %d threads, %d blocks x 128MiB, %d min%n",
                totalGiB, nThreads, totalBlocks, minutes);

        Thread[] threads = new Thread[nThreads];
        for (int t = 0; t < nThreads; t++) {
            final int tid = t;
            threads[t] = new Thread(() -> {
                long[][] blocks = new long[blocksPerThread][];
                for (int b = 0; b < blocksPerThread; b++) {
                    blocks[b] = new long[wordsPerBlock];
                }
                long pass = 0;
                while (System.currentTimeMillis() < deadline) {
                    pass++;
                    for (int b = 0; b < blocksPerThread; b++) {
                        long blockId = tid * 100_000L + b;
                        long[] blk = blocks[b];
                        for (int i = 0; i < blk.length; i++) {
                            blk[i] = mix(blockId, pass, i);
                        }
                    }
                    try { Thread.sleep(2000); } catch (InterruptedException e) { return; }
                    for (int b = 0; b < blocksPerThread; b++) {
                        long blockId = tid * 100_000L + b;
                        long[] blk = blocks[b];
                        for (int i = 0; i < blk.length; i++) {
                            long expect = mix(blockId, pass, i);
                            if (blk[i] != expect) {
                                long bad = blk[i];
                                errors.incrementAndGet();
                                System.out.printf(
                                    "MEM ERROR thread=%d block=%d word=%d expect=%016x got=%016x xor=%016x%n",
                                    tid, b, i, expect, bad, expect ^ bad);
                            }
                        }
                    }
                    long p = passes.incrementAndGet();
                    if (p % nThreads == 0) {
                        System.out.printf("[%tT] pass ~%d/thread done, errors=%d%n",
                                System.currentTimeMillis(), p / nThreads, errors.get());
                    }
                }
            }, "stress-" + t);
            threads[t].start();
        }
        for (Thread th : threads) th.join();
        System.out.printf("DONE passes=%d errors=%d%n", passes.get(), errors.get());
        if (errors.get() > 0) System.exit(3);
    }
}
