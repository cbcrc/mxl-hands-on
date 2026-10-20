# The Journey Across the Socket: Measuring Cross-NUMA Costs in MXL

**Reference:** For full methodology, raw data, and hardware specifics, please refer to the detailed technical document: `numa-results.md`.

## The Core Question

Modern servers are essentially two computers glued together. Our lab hosts feature dual Intel Sapphire Rapids chips (Sockets 0 and 1), each with its own cores, cache, and memory. They talk to each other over a bridge called UPI.

We wanted to precisely quantify the overhead: **What is the exact latency cost when a video flow is written to memory on Socket 0, but read by a consumer sitting on Socket 1?** While we already knew crossing this boundary wouldn't break our strict 33.4 millisecond frame budget for p30 video, we needed accurate measurements of the UPI bridge's toll on our processing pipeline.

To measure this cross-NUMA cost, we ran a series of tests. But the hardware didn't give up its secrets easily.

## Plot Twist 1: The Governor's Deception

Our very first test yielded a bizarre result: reading a frame across the socket boundary actually looked *faster* than reading it locally.

This turned out to be an illusion caused by the CPU power governor (`schedutil`). Because our readers have to wait 33 milliseconds between frames, the CPU cores were clocking down to save power. Whether a thread woke up on a "fast" core or a "slow" core was pure luck, causing massive, 2x variations in read times.

**The Fix:** We pinned the CPUs to the `performance` governor. The noise vanished, and wake-up latencies immediately dropped by half.

## Plot Twist 2: Escaping the L3 Cache

With the CPU speeds locked in, we tested again using 1080p video. The results were incredibly optimistic—too optimistic. It turns out the entire 5-frame ring buffer of a 1080p flow (about 27.6 MB) fits perfectly inside the CPU's 30 MB L3 cache. We weren't measuring remote memory transit at all; we were just measuring cache speed!

To escape the cache, we moved to massive 4K (2160p) video, where a single frame is 22 MB. But the CPU caught us again: if a consumer reads the *newest* available frame right after it is committed, that data is still sitting warm in the writer's L3 cache. 

**The Fix:** We had to deliberately lag our reader by 3 frames (`head-3`). At 4K, writing three newer frames generates 66 MB of data—more than enough to completely flush our target frame out of the 30 MB L3 cache and force a true, punishing read from DRAM.

## Plot Twist 3: The Peer-to-Peer Cache Illusion

Even with 4K video and a lagged reader, the cross-socket penalty still looked remarkably low (only a +4% latency bump). 

We were fooled a third time. We had multiple test readers running at once. Our telemetry tools revealed that the readers were secretly feeding each other over the UPI bridge. Once the first reader pulled the frame from DRAM, the other readers just copied the data out of that first reader's speedy cache rather than making the long trek to remote memory.

**The Fix:** We isolated a single reader on Socket 1 to force a lonely, true remote memory read.

## The Truth Revealed: The Real Cost of Crossing

With all the cache illusions stripped away, the true cost of crossing the NUMA boundary finally emerged. When a solitary reader on Socket 1 pulls a 2160p frame from Socket 0's DRAM, two things happen:

1. **The Reader Pays a Toll:** The remote copy takes about **0.44 ms longer** (+30%) than a local copy.

2. **The Writer Gets Slowed Down:** Surprisingly, the writer on Socket 0 also slows down by about **0.5 ms** (+34%). Why? Because the remote reader leaves a "breadcrumb" in the memory directory. When the writer tries to overwrite that memory slot for a future frame, it has to shout across the bridge to Socket 1 ("Hey, do you still have a copy of this?"), wait for an empty response, and then rewrite the directory.

Combined, crossing the socket costs roughly **1 millisecond** of latency per frame.

## Stressing the Bridge

As expected, a 1 millisecond penalty is highly manageable within a 33.4 ms frame window. But what happens to that latency if we scale up? We pushed the system with up to 6 remote readers, 4 heavy 2160p flows, and massive artificial memory stress (pushing 95 GB/s of traffic).

The good news: **The bridge holds up.**

* **The cost is per-flow, not per-reader.** Adding more remote readers to a single flow doesn't increase the latency penalty for the writer, because the frame only needs to cross the UPI bridge once.

* Even under the most extreme load tests, the cross-NUMA latency degraded at about the same rate as local reads. The absolute worst-case processing delays stayed **under 5 milliseconds**, confirming our pipeline remains completely safe even under heavy system stress.

## Key Takeaways & Rules for Deployment

Based on the investigation, here are the simple rules of thumb for deploying MXL pipelines on this hardware:

1. **Rule #1: Tame the Governor.** You must pin the CPU governor to `performance` on MXL hosts. Forgetting to do this causes latency spikes far worse than any NUMA boundary penalty.

2. **Keep it Local When Convenient.** If you can easily keep a flow's writer and its consumers on the same socket, do it. It saves about 1 ms of latency per 2160p frame.

3. **Group Remote Readers.** If a flow *must* cross to the other socket, try to put all of its remote consumers on the same socket. You only pay the crossing toll once per flow.

4. **Don't Fear the Bridge.** If you need to cross the NUMA boundary to balance CPU loads, go ahead. The latency overhead is clearly mapped and comfortably within limits.