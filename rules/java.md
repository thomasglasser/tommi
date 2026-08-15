# Java Standards & Language Conventions

## 1. Collections & FastUtil
* **Avoid Default Collections**: Avoid standard `ArrayList` and `HashMap` in favor of FastUtil or Guava equivalents (e.g., `ObjectArrayList`, `ReferenceOpenHashSet`, `Object2ObjectOpenHashMap`) to minimize boxing and memory overhead.
* **Immutability for Public APIs**: Use `ImmutableList`, `ImmutableSet`, or `ImmutableMap` when returning collections in public APIs to prevent unintended mutations.

## 2. Language Features & APIs
* **Avoid Streams in Hot Paths**: Avoid using the Java Streams API (`.stream().filter()...`), particularly in frequently executed code or per-tick loops. Use direct for-loops or FastUtil iterators.
* **No `var` Keyword**: Do NOT use `var`. Always declare explicit types for clarity and reviewability.
* **No Static Imports**: Always import classes explicitly and call static methods with class qualification (e.g., `Collections.emptyList()` rather than `emptyList()`).
* **Generics**: Avoid raw types. Use `<?>` or properly bounded wildcards when the exact type parameter is not constrained.
* **Check Effect Pattern**: NEVER call `entity.hasEffect(Effect)` followed by `entity.getEffect(Effect)`. ALWAYS fetch the `MobEffectInstance` into a variable and check for `!= null`.
