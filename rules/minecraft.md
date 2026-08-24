# Minecraft & NeoForge Modding Best Practices

## 1. Client vs. Server Separation & Side Safety

* **ServerLevel Nullability**: `ServerLevel#getServer()` is guaranteed to be non-null. NEVER suggest null-checking `ServerLevel#getServer()` or flag calls on it as potential `NullPointerException`s.
* **Dedicated Server Safety**: NEVER use client-only classes (`Minecraft`, `KeyMapping`, `LocalPlayer`, GUI screens) in common code. This will crash dedicated servers.
* **Side-Safe Access**: Use client-safe utilities (such as `ClientUtils.getLocalPlayer()` or distribution executors) when referencing client objects from shared logic.
* **Server Authority**: Perform physics, state mutations, movement delta calculations, and game logic on the server, not the client.

## 2. Registry & Data Management
* **Holder Usage**: ALWAYS prefer `Holder<T>` over raw object references, `ResourceLocation`s, or string IDs when referencing data-driven registry entries.
* **Data-Driven Design with Tags**: NEVER hardcode specific items or blocks in logic. Create and use tags (e.g., `hibiscus_bushes`, `removed_by_rinsing`) to ensure extensibility and mod interoperability.
* **Constants**: Use vanilla and NeoForge constants wherever possible (e.g., `Block` constants in `setBlock`, `SharedConstants.TICKS_PER_SECOND` for AI timeouts/cooldowns).
* **Built-in & Library Utils**: Leverage existing library utilities (such as `TommyLib`, `AnimationUtils`, `StreamCodecs`) instead of duplicating functionality.
* **Data Attachments**:
  * When reading data attachments, avoid duplicate lookups (e.g. store `player.getData(...)` in a variable).
  * Do NOT call `remove()` repeatedly if the attachment is absent; check `.isPresent()` first.

## 3. Rendering & GUI Standards
* **GUI Lighting**: When rendering items or custom models in a GUI context (e.g., `BlockEntityWithoutLevelRenderer`, `GeoItemRenderer`), ALWAYS force the light level to `LightTexture.FULL_BRIGHT` (`15728880`).
* **GUI Translucency & Culling**: When rendering flat or custom 2D geometry in GUI, NEVER use a `RenderType` with culling enabled. The GUI's flipped Y-axis scale inverses screen-space winding orders. Use non-culling render types.

## 4. Naming & Terminology

* **Event Handlers**: Do NOT include `Event` in event handler method names. Name event handlers by prefixing with `on` and working backwards through nested event class names (e.g., `MiraculousEvent.Transform.Finish` becomes `onFinishTransformMiraculous`, `LivingFallEvent` becomes `onLivingFall`).
* **Event Handlers**: Do NOT include `Event` in event handler method names (e.g., use `onLivingFall` instead of `onLivingFallEvent`).
* **Avoid "Tool" for Non-Tools**: Reserve the word "tool" strictly for actual tools (pickaxes, axes, shovels). Do NOT use it for miscellaneous, throwable, or magical items.

## 5. APIs

* **Side-Effectful Method Calls & Cache Checks**: Do NOT flag `containsKey` or presence checks followed by method calls (e.g., `getBakedModel`) as duplicate lookups when the called method performs essential side effects, initialization, or fallback logic that direct map retrieval (`get()`) bypasses.

* **Mob Effect Comparisons**: Do NOT flag explicit or non-standard comparisons involving `MobEffect`, `MobEffectInstance`, or `Holder<MobEffect>` as redundant; Mob Effect matching has quirks requiring specific comparison logic to remain accurate.
* **Check Effect Pattern**: NEVER call `entity.hasEffect(Effect)` followed by `entity.getEffect(Effect)`. ALWAYS fetch the `MobEffectInstance` into a variable and check for `!= null`.
