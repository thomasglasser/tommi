# Performance & Memory Management

## 1. Tick Loops & High-Frequency Operations
* **Zero Object Allocations in Ticks**: NEVER instantiate new objects (e.g. `new ResourceLocation(...)`, `new Vec3(...)`, `new BlockPos(...)`, or lambdas) inside `tick()` or other per-frame/per-tick methods. Cache them as constants or fields.
* **BlockPos.Mutable**: ALWAYS use `BlockPos.Mutable` when iterating or scanning coordinates instead of allocating new `BlockPos` objects on every step.
* **Throttling**: Throttle expensive repeating checks (e.g. `if (entity.tickCount % 10 == 0)` or `SharedConstants.TICKS_PER_SECOND` intervals) rather than evaluating them every tick.
* **Hoist Invariants**: Move expensive invariant checks out of loops (e.g. checking whether a screen is open or level is valid should happen before a loop, not inside).
