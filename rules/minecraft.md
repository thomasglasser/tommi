# Minecraft & NeoForge Modding Best Practices

## 1. Client vs. Server Separation & Side Safety

* **Clientbound Packets & Client Utilities**: Do NOT flag calls to client utility classes (e.g., `*ClientUtils`) inside clientbound packet handlers (`handle` methods on clientbound payloads) as side-unsafe; clientbound packet execution occurs exclusively on the client side.

* **ServerLevel Nullability**: `ServerLevel#getServer()` is guaranteed to be non-null. NEVER suggest null-checking `ServerLevel#getServer()` or flag calls on it as potential `NullPointerException`s.
* **Dedicated Server Safety**: NEVER use client-only classes (`Minecraft`, `KeyMapping`, `LocalPlayer`, GUI screens) in common code. This will crash dedicated servers.
* **Side-Safe Access**: Use client-safe utilities (such as `ClientUtils.getLocalPlayer()` or distribution executors) when referencing client objects from shared logic.
* **Server Authority**: Perform physics, state mutations, movement delta calculations, and game logic on the server, not the client.

## 2. Registry & Data Management

* **Access Transformers vs. Copying Constants**: When extending or copying vanilla screens, entities, or other vanilla logic, ALWAYS prioritize using Access Transformers (`accesstransformer.cfg`) to expose vanilla fields, methods, or constants rather than copying/duplicating their values into local constants.

* **Reversion & Undo Collection Snapshots**: Do NOT flag getter methods or calls returning collection snapshots (e.g., `ImmutableList` snapshots from `getAllEntries()`) as redundant or inefficient allocations outside hot tick loops; snapshots are required to prevent `ConcurrentModificationException` when entries are mutated during iteration.

* **Reversion & Undo Scans**: Do NOT suggest early loop termination (`break` or `return`) or flag duplicate processing when iterating over reversion entries, undo queues, or history logs for a given entity or target; multiple entries can legitimately apply to the same entity (such as chained conversions or re-reversions) and must be processed.

* **Item & Inventory Searches**: Do NOT flag entity or block-entity inventory scans as redundant or suggest early loop termination (`break`, `return`, or `if (!found)` guards); item stacks with matching IDs or data components can be split, duplicated, or distributed across multiple inventories and entities.

* **ItemStack Equality & Collections**: Do NOT flag `ObjectOpenHashSet<ItemStack>` or `ReferenceOpenHashSet<ItemStack>` as using flawed equality checks; in Minecraft 1.21+, `ItemStack` does not override `equals()` or `hashCode()`, so sets compare `ItemStack` instances by identity.

* **SavedData Serialization**: Do NOT flag `.getOrThrow()` calls on `Codec` operations within `SavedData` `save()` or `load()` methods as unhandled exceptions or dangerous; Minecraft's `SavedData` system handles serialization errors internally.
* **Holder Usage**: ALWAYS prefer `Holder<T>` over raw object references, `ResourceLocation`s, or string IDs when referencing data-driven registry entries.
* **Data-Driven Design with Tags**: NEVER hardcode specific items or blocks in logic. Create and use tags (e.g., `hibiscus_bushes`, `removed_by_rinsing`) to ensure extensibility and mod interoperability.
* **Conventional Tag Holder Helpers**: In tag holder classes that declare conventional (`"c"`) tags, ALWAYS define a `createC(String path)` helper delegating to the mod's conventional tag utility. Do NOT pass conventional namespace constants directly in field declarations.
* **Empty Datagen Provider Elimination**: Do NOT create or register empty datagen tag provider classes when no tags are defined for that registry. Pass `CompletableFuture.completedFuture(null)` for unused provider dependencies.
* **Constants**: Use vanilla and NeoForge constants wherever possible (e.g., `Block` constants in `setBlock`, `SharedConstants.TICKS_PER_SECOND` for AI timeouts/cooldowns).
* **Built-in & Library Utils**: Leverage existing library utilities (such as `TommyLib`, `AnimationUtils`, `StreamCodecs`) instead of duplicating functionality.
* **Data Attachments**:
  * When reading data attachments, avoid duplicate lookups (e.g. store `player.getData(...)` in a variable).
  * Do NOT call `remove()` repeatedly if the attachment is absent; check `.isPresent()` first.

## 3. Rendering & GUI Standards
* **GUI Lighting**: When rendering items or custom models in a GUI context (e.g., `BlockEntityWithoutLevelRenderer`, `GeoItemRenderer`), ALWAYS force the light level to `LightTexture.FULL_BRIGHT` (`15728880`).
* **GUI Translucency & Culling**: When rendering flat or custom 2D geometry in GUI, NEVER use a `RenderType` with culling enabled. The GUI's flipped Y-axis scale inverses screen-space winding orders. Use non-culling render types.

## 4. Naming & Terminology
* **Tool vs. Weapon Terminology**: Use the term "tool" for items that have utility or gameplay interactions beyond combat (e.g., axes, swords due to web clearing, or yoyos/canes/staves with non-combat utility). Reserve "weapon" strictly for items with exclusively combat uses (e.g., a mace). Do NOT flag items with non-combat utility or interaction mechanics as incorrectly using the term "tool", but avoid using "tool" for purely miscellaneous, throwable, or decorative items.
* **Event Listener Method Naming**: Do NOT include `Event` in event listener method names. Event listener method names must follow the Java class hierarchy from left to right:
  1. **Flat Events (non-nested classes)**:
     Use `on` + the event class name (omitting `Event`):
     - `RegisterCommandsEvent` -> `onRegisterCommands` (never invert words like `onCommandsRegister`)
     - `ServerStartedEvent` -> `onServerStarted`
     - `BlockDropsEvent` -> `onBlockDrops`
     - `VillagerTradesEvent` -> `onVillagerTrades`
  2. **Nested Events (hierarchical classes)**:
     Use `on` + `[Phase]` + `OuterClass` (Domain) + `InnerClass` (Action):
     - `BlockEvent.BreakEvent` -> `onBlockBreak` (Domain `Block` + Action `Break`)
     - `ChunkEvent.Load` -> `onChunkLoad` (Domain `Chunk` + Action `Load`)
     - `LuckyCharmEvent.DetermineTarget` -> `onLuckyCharmDetermineTarget` (Domain `LuckyCharm` + Action `DetermineTarget`)
     - `EntityTickEvent.Pre` -> `onPreEntityTick`
     - `MiraculousEvent.Transform.Pre` -> `onPreMiraculousTransform` (Domain `Miraculous` + Action `Transform`)
     - `MiraculousEvent.Transform.Trigger` -> `onTriggerMiraculousTransform`
     - `MiraculousEvent.Detransform.Pre` -> `onPreMiraculousDetransform`
     - `KamikotizationEvent.Transform.Pre` -> `onPreKamikotizationTransform`
     - `StealEvent.Start.Pre` -> `onPreStealStart`
  * **Rule**: Never place the domain class name at the end of a method name (e.g., do NOT recommend `onPreTransformMiraculous`, `onBreakBlock`, or `onCommandsRegister`).
* **Event Listener Deduplication**: Do NOT flag event listener method names that deduplicate repeated words between outer and inner event classes (e.g., `onItemCanBreak` for `ItemBreakEvent.CanBreak` instead of `onItemBreakCanBreak`). Omitting redundant or repeated words across the class hierarchy is acceptable.

## 5. APIs
* **Level Entity Retrieval by UUID**: Do NOT suggest calling `level.getEntity(uuid)` on `Level` or `player.level()`; `Level` does not have a `getEntity(UUID)` method. Entity retrieval by `UUID` on standard `Level` instances requires `level.getEntities().get(uuid)` (or casting to `ServerLevel`).
* **Dimension-Local Entity Lookups**: Do NOT suggest replacing `level.getEntity(...)` or dimension-local entity lookups with global search utilities (such as `MineraculousEntityUtils.findLivingEntity`) when the lookup is scoped to the current level; global entity utilities search across all dimensions on the server and have different scoping behavior.
* **ItemEntity Empty Stack Discarding**: Do NOT flag or suggest calling `discard()` on an `ItemEntity` when setting its stack to empty or clearing its slot; `ItemEntity` automatically checks if its stack is empty during its tick and handles discarding itself.
* **Check Effect Pattern & hasEffect**: NEVER call `entity.hasEffect(Effect)` followed by `entity.getEffect(Effect)`. When effect properties (duration/amplifier) are needed, ALWAYS fetch the `MobEffectInstance` into a single variable and check for `!= null`. However, when only checking effect presence, using `entity.hasEffect(Effect)` directly is correct; do NOT flag `hasEffect` when `getEffect` is not called.
* **Mob Effect Comparisons & Matching**: Do NOT flag explicit, manual iteration, or non-standard comparisons involving `MobEffect`, `MobEffectInstance`, `Holder<MobEffect>`, or `HolderSet<MobEffect>` (e.g., manually iterating over a `HolderSet` and comparing references, `.value()`, or keys instead of using `HolderSet#contains` or `Holder#is`) as redundant or convoluted; Mob Effect matching has quirks requiring specific comparison logic to remain accurate.
* **Side-Effectful Method Calls & Cache Checks**: Do NOT flag `containsKey` or presence checks followed by method calls (e.g., `getBakedModel`) as duplicate lookups when the called method performs essential side effects, initialization, or fallback logic that direct map retrieval (`get()`) bypasses.
* **Return Types**: ALWAYS verify what methods return instead of assuming (for example, a `level()` method could return a ServerLevel instead of a Level, so it has certain non-nullability of the server).

