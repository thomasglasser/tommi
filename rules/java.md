# Java Standards & Language Conventions

## 1. Collections & FastUtil

* **LinkedHashSet & Sequenced Collections**: Do NOT flag standard `LinkedHashSet` as a violation of FastUtil collection rules when sequenced operations (such as `reversed()`) or `SequencedSet` compatibility are required; FastUtil's `ObjectLinkedOpenHashSet` does not support these methods.

* **Avoid Default Collections**: Avoid standard `ArrayList` and `HashMap` in favor of FastUtil or Guava equivalents (e.g., `ObjectArrayList`, `ReferenceOpenHashSet`, `Object2ObjectOpenHashMap`) to minimize boxing and memory overhead.
* **Immutability for Public APIs**: ALWAYS use Guava immutable collection types (`ImmutableList`, `ImmutableSet`, `ImmutableMap`) in return types and implementations for public APIs. NEVER use or suggest `Collections.unmodifiable*` wrappers as they obscure immutability in the API signature.
* **Avoid Default Collections**: Avoid standard `ArrayList` and `HashMap` in favor of FastUtil or Guava equivalents (e.g., `ObjectArrayList`, `ReferenceOpenHashSet`, `Object2ObjectOpenHashMap`) to minimize boxing and memory overhead.
* **Immutability for Public APIs**: Use `ImmutableList`, `ImmutableSet`, or `ImmutableMap` when returning collections in public APIs to prevent unintended mutations.

## 2. Language Features & APIs

* **Optional Unwrapping Shadowing**: Do NOT flag local variables that shadow an `Optional` field or parameter when storing the unwrapped value (e.g., `HolderSet<Kamikotization> kamikotizations = this.kamikotizations.get();`); unwrapping an `Optional` into a local variable of the same name is acceptable.

* **Javadoc Scope**: Javadocs are NOT required on private members, private helper methods, or private inner classes/records. Do NOT suggest adding Javadocs to private members.

* **Single-Line vs. Multi-Line Javadocs**: Use `///` EXCLUSIVELY for single-line Javadocs. Multi-line Javadocs MUST use standard `/** ... */` block syntax. NEVER use or suggest `///` for multi-line comments, and NEVER suggest converting multi-line `/** ... */` Javadocs to `///`.

* **Collection & Iterable forEach**: Do NOT flag `.forEach(...)` calls on collections or iterables with single-statement lambdas as anti-patterns or suggest converting them to enhanced for-loops outside of per-tick hot paths; single-statement `forEach` calls are acceptable.

* **Optional.ifPresent Usage**: Do NOT flag `Optional.ifPresent(...)` or suggest replacing it with `isPresent()` and `.get()` checks to avoid lambda allocations; `ifPresent` is cleaner and the performance overhead is negligible outside of per-tick hot paths.

* **No Java Streams**: NEVER use the Java Streams API (`.stream().filter()...`), even in one-off events, infrequent handlers, or non-performance-sensitive code. ALWAYS use traditional for-loops, enhanced for-loops, or FastUtil iterators for consistency and assurance.

* **Streams in One-Off Events**: Do NOT flag Java Stream API usage in one-off, infrequent event handlers (e.g., `LuckyCharmEvent` or on-demand ability triggers) that are not performance-sensitive or executed per-tick.

* **Generic Shadowing & Type Checks**: Do NOT flag `instanceof` checks or type casts on generic type parameters inside static methods as redundant when the static method declares its own generic type parameter that shadows or differs from class-level generics.
* **Avoid Streams in Hot Paths**: Avoid using the Java Streams API (`.stream().filter()...`), particularly in frequently executed code or per-tick loops. Use direct for-loops or FastUtil iterators.
* **Generics**: Avoid raw types. Use `<?>` or properly bounded wildcards when the exact type parameter is not constrained.
* * **Javadocs**:
  * Use `///` exclusively for single-line javadocs. Use `//` for inline implementation notes (e.g., `// TODO`).
  * Class javadocs are expected on APIs and core abstractions, but implementation details (`impl`) do not need them unless necessary.
  * Place separate sentences on separate lines within javadocs.
  * Place javadocs on the methods/classes themselves, not on registry entries.
  * ALWAYS adhere to standard American English spelling and grammar in Javadocs, comments, and parameter descriptions.
