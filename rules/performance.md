# Performance & Memory Management

## 1. Tick Loops & High-Frequency Operations
* **Zero Object Allocations in Ticks**: NEVER instantiate new objects (e.g. `new ResourceLocation(...)`, `new Vec3(...)`, `new BlockPos(...)`, or lambdas) inside `tick()` or other per-frame/per-tick methods. Cache them as constants or fields.
* **Behavior & Goal Constructor Lambdas**: Do NOT flag lambda closures or function allocations in AI behavior/goal constructors (e.g., `runFor(entity -> ...)` in `ExtendedBehaviour`) as prohibited allocations or stale closures; behaviors are constructed during entity/brain registration (not per-tick), and lambdas referencing instance fields capture the field reference dynamically on execution.
* **BlockPos.MutableBlockPos**: ALWAYS use `BlockPos.MutableBlockPos` (or `BlockPos.betweenClosedStream` / mutable iterators) when iterating or scanning coordinates instead of allocating new `BlockPos` objects on every step. Do NOT suggest or rename `BlockPos.MutableBlockPos` to non-existent class names like `BlockPos.Mutable`.
* **Spline & Particle Operations in Ticks**: Do NOT flag client-side particle spawning or standard spline entity calculations (e.g., target distance queries, segment tracking, facing direction updates) as prohibited tick allocations or performance violations without verifying that new heap objects are actually being allocated.
* **Throttling**: Throttle expensive repeating checks (e.g. `if (entity.tickCount % 10 == 0)` or `SharedConstants.TICKS_PER_SECOND` intervals) rather than evaluating them every tick.
* **Hoist Invariants**: Move expensive invariant checks out of loops (e.g. checking whether a screen is open or level is valid should happen before a loop, not inside).

