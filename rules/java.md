# Java Standards & Language Conventions

## 1. Collections & FastUtil
* **Avoid Default Collections**: Avoid standard `ArrayList` and `HashMap` in favor of FastUtil or Guava equivalents (e.g., `ObjectArrayList`, `ObjectOpenHashSet`, `Object2ObjectOpenHashMap`) to minimize boxing and memory overhead.
* **Intelligent Selection: `ObjectOpenHashSet` vs. `ReferenceOpenHashSet`**:
  * **General Default (`ObjectOpenHashSet` / `Object2*OpenHashMap`)**: Use `ObjectOpenHashSet` by default for general objects, API-facing sets, records, holders (`ExtendedHolder`), and any types that implement value-based `equals()` and `hashCode()`.
  * **When to Prefer `ReferenceOpenHashSet` (Peak Performance)**: `ReferenceOpenHashSet` uses pointer equality (`==`) and `System.identityHashCode()`, which is significantly faster than virtual `equals()` calls. Strongly prefer `Reference*` collections ONLY when the element/key type is guaranteed to be:
    1. **Singleton Registry Objects**: Vanilla and mod registry entries that exist as single instances in memory (e.g., `Item`, `Block`, `MobEffect`, `EntityType`, `SoundEvent`, `Enchantment`).
    2. **Java Enums**: Enum constants (e.g., `Direction`, `DyeColor`, `InteractionHand`, `EquipmentSlot`).
    3. **Live Entity & Instance Tracking**: Active runtime instances where identity comparison is intentional (e.g., `Entity`, `Player`, `BlockEntity`, `Level`, `AbstractContainerMenu`).
  * **When MUST Use `ObjectOpenHashSet` (Value Equality)**: NEVER suggest `Reference*` collections for types where distinct object instances can represent the same logical value:
    1. Identifiers & Keys: `ResourceLocation` / `Identifier`, `ResourceKey`, `TagKey`.
    2. Value Wrappers & Holders: `ExtendedHolder`, `Holder`, `Optional`, custom wrapper records.
    3. Primitives & Math: `String`, `UUID`, `BlockPos`, `Vec3`, `Vector3f`, components.
* **Immutability for Public APIs**: ALWAYS prefer Guava immutable collection types (`ImmutableList`, `ImmutableSet`, `ImmutableMap`) or custom unmodifiable wrapper classes that clearly communicate immutability in their class name/API (e.g., `ImmutableCollectionView`) for public APIs. Custom immutable collection views are completely acceptable as long as their immutability is clear. NEVER use or suggest raw `Collections.unmodifiable*` wrappers that return generic mutable `Collection<E>` / `List<E>` types as API signatures.
* **LinkedHashSet & Sequenced Collections**: Do NOT flag standard `LinkedHashSet` as a violation of FastUtil collection rules when sequenced operations (such as `reversed()`) or `SequencedSet` compatibility are required; FastUtil's `ObjectLinkedOpenHashSet` does not support these methods.

## 2. Language Features & APIs
* **Javadoc & Documentation Comment Conventions**:
  * **Java 21 vs. Java 23+ (Markdown Javadocs)**:
    * **Java 21 (e.g. Minecraft 1.21.x)**: ALWAYS use standard Javadoc syntax. Use `/** javadoc */` single-line format exclusively for concise single-line Javadocs to remain clean without being messy. NEVER use `///` comments in Java 21 as they are treated as non-doc comments. Multi-line documentation MUST use standard `/** ... */` block syntax.
    * **Java 23+ (e.g. Minecraft 26.1 which uses Java 25)**: EXCLUSIVELY use Markdown documentation comments (`///`) for all single-line and multiline documentation. NEVER use legacy `/** ... */` syntax in Java 23+ codebases.
  * **Javadoc Scope**: Javadocs are NOT required on private members, private helper methods, or private inner classes/records. Do NOT suggest adding Javadocs to private members.
  * Class Javadocs are expected on APIs and core abstractions, but implementation details (`impl`) do not need them unless necessary.
  * Place separate sentences on separate lines within Javadocs.
  * Place Javadocs on the methods/classes themselves, not on registry entries.
  * ALWAYS adhere to standard American English spelling and grammar in Javadocs, comments, and parameter descriptions.
* **Optional Usage**:
  * **Optional Unwrapping Shadowing**: Do NOT flag local variables that shadow an `Optional` field or parameter when storing the unwrapped value (e.g., `HolderSet<Kamikotization> kamikotizations = this.kamikotizations.get();`); unwrapping an `Optional` into a local variable of the same name is acceptable.
  * **Optional.ifPresent Usage**: Do NOT flag `Optional.ifPresent(...)` or suggest replacing it with `isPresent()` and `.get()` checks to avoid lambda allocations; `ifPresent` is cleaner and the performance overhead is negligible outside of per-tick hot paths.
* **Java Streams & Iteration**:
  * **Avoid Streams in Hot Paths**: Avoid using the Java Streams API (`.stream().filter()...`) in frequently executed code or per-tick loops; use direct for-loops or FastUtil iterators.
  * **Streams in One-Off Events**: Do NOT flag Java Stream API usage in one-off, infrequent event handlers (e.g., `LuckyCharmEvent` or on-demand ability triggers) or datagen providers that are not performance-sensitive or executed per-tick.
  * **Collection & Iterable forEach**: Do NOT flag `.forEach(...)` calls on collections or iterables with single-statement lambdas as anti-patterns or suggest converting them to enhanced for-loops outside of per-tick hot paths; single-statement `forEach` calls are acceptable.
* **Generics**:
  * Avoid raw types. Use `<?>` or properly bounded wildcards when the exact type parameter is not constrained.
  * **Generic Shadowing & Type Checks**: Do NOT flag `instanceof` checks or type casts on generic type parameters inside static methods as redundant when the static method declares its own generic type parameter that shadows or differs from class-level generics.
* **Unnamed Variables & Lambda Parameters (`_`)**:
  * **Java 21 Incompatibility (e.g. Minecraft 1.21.x)**: Unnamed variables and patterns (`_`) are a preview feature in Java 21 (JEP 443) and are NOT enabled in standard Java 21 builds. Using `_` as a variable or lambda parameter causes compilation errors (`as of release 9, '_' is a keyword`).
  * **NEVER Suggest `_` in Java 21**: NEVER instruct authors to replace unused lambda parameters, exception variables, or method parameters with `_` in Java 21 / 1.21.x codebases.
  * **Functional Interface Conformance**: When a lambda implements a functional interface taking parameters that the lambda body does not consume (e.g. `key -> sup.get()` for `Function<ResourceKey<T>, T>`), keeping the named parameter (such as `key` or `ignored`) is standard, correct, and MUST NOT be flagged as an unused variable.

