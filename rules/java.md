# Java Standards & Language Conventions

## 1. Collections & FastUtil
* **Avoid Default Collections**: Avoid standard `ArrayList` and `HashMap` in favor of FastUtil or Guava equivalents (e.g., `ObjectArrayList`, `ReferenceOpenHashSet`, `Object2ObjectOpenHashMap`) to minimize boxing and memory overhead.
* **Immutability for Public APIs**: Use `ImmutableList`, `ImmutableSet`, or `ImmutableMap` when returning collections in public APIs to prevent unintended mutations.

## 2. Language Features & APIs

* **Generic Shadowing & Type Checks**: Do NOT flag `instanceof` checks or type casts on generic type parameters inside static methods as redundant when the static method declares its own generic type parameter that shadows or differs from class-level generics.
* **Avoid Streams in Hot Paths**: Avoid using the Java Streams API (`.stream().filter()...`), particularly in frequently executed code or per-tick loops. Use direct for-loops or FastUtil iterators.
* **Generics**: Avoid raw types. Use `<?>` or properly bounded wildcards when the exact type parameter is not constrained.
* * **Javadocs**:
  * Use `///` exclusively for single-line javadocs. Use `//` for inline implementation notes (e.g., `// TODO`).
  * Class javadocs are expected on APIs and core abstractions, but implementation details (`impl`) do not need them unless necessary.
  * Place separate sentences on separate lines within javadocs.
  * Place javadocs on the methods/classes themselves, not on registry entries.
