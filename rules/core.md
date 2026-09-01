# Core Code Style, Organization & Etiquette

## 1. Naming & Terminology
* **Descriptive & Action-Oriented**: Avoid vague names (`null`, `trigger`, `commander`, `d`, `a`, `g`). Use explicit, action-oriented names (e.g., `enqueueTransformation` instead of `transform`, `normalizeDegreesBetween180` instead of `normalizeDegrees`).
* **No Abbreviations**: Do NOT abbreviate variable names (e.g., avoid `MLB` for Miraculous Ladybug, or `ent` for `entity`). Use the full word.
* **Java Naming Conventions**: Strictly follow standard Java conventions (camelCase methods/variables, PascalCase types).
* **Constant Naming**: `public static final` (PSF) fields MUST use `CAPITAL_SNAKE_CASE`.
* **Pluralization**: Be accurate with plurals (e.g., `miraculouses`, `options` instead of `option` for collections).

## 2. Layout & Structure

* **Static Factory & Builder Placement**:
  * Static builder accessors (e.g., `builder()`) may be placed directly above the nested builder class/record definition.
  * Static factory/constructing helpers (e.g., `of(...)`, `perching(...)`, `create(...)`) MUST be placed directly below the constructors and before instance methods.

* **Method Organization**: Keep method placement clean and navigable. Do NOT strictly enforce visibility order (e.g. public before private); helper, protected, or private methods may be placed directly below where they are used or organized logically for readability.

* **Compound Condition Branching**: Do NOT flag condition checks in `else` or `else if` branches as redundant when the preceding `if` statement uses a compound boolean condition (e.g., `&&` or `||`); entering the `else` branch only implies that the overall condition evaluated to false, not which specific sub-condition failed.

* **Local Classes in Methods**: Do NOT flag local classes declared inside methods when used for lambda workarounds or local context as misplaced inner classes.
* **Logical Ordering**:
  * ALWAYS group related items logically and consistently.
* **Class Layout**:
  * **Public before Private**: Public methods and constructors MUST be placed above private ones.
  * **Inner Classes, Records & Enums**: MUST be placed below all methods at the very bottom of the class.
  * **Variable Placement**: Declare variables right above where they are used, rather than at the top of a method.
* **Control Flow**:
  * Use `else if` chains rather than isolated `if` statements when branching on the same condition.
  * Combine boolean conditions with `&&` and `||` wherever possible to avoid unnecessary nested `if` statements.
* **Spacing**: Avoid unnecessary blank lines. Keep closely related logic tightly grouped.

## 3. Code Cleanliness & DRY
* **Inlining**: Inline variables and methods that are only used once or merely wrap a single call.
* **DRY (Don't Repeat Yourself)**: Extract duplicated logic into parent classes or utility methods.
* **No Redundant Overrides**: If an overridden method only calls `super.method()`, remove the override entirely.
* **Remove Dead Code**: NEVER commit commented-out code, unused variables, or unused generic parameters.
* **No `var`**: Do NOT use the `var` keyword in Java. Explicitly define variable types.
* **Static Imports**: Do NOT use static imports.
* **No Fully Qualified Inline Names**: Import classes at the top of the file rather than referencing them inline.
* **Local Methods**: Do NOT use `this.` prefix for method calls within the same class unless resolving a naming collision or shadowing.
* **Return Types**: Design methods to return informative types or booleans (e.g., success/failure or cancellation) rather than relying on side effects.

## 4. Git & Review Etiquette
* **Pay Attention to IDE Warnings**: Never ignore yellow/red IDE warnings (e.g., comparing `ResourceKey` to `ResourceLocation`).
* **Self-Review**: Contributors MUST review their own PR diff before requesting review.
* **Complete Fixes**: Do not mark review comments as resolved without actually fixing the underlying issue.
* **Trust the Compiler**: All pull requests are verified to compile cleanly with javac/Gradle prior to review. NEVER report compilation errors, syntax errors, duplicate method/field declarations, or missing imports. Do not mistake a method call in an expression for a duplicate declaration.
* **Accurate Unused Parameter Checks**: NEVER claim a parameter or variable is unused without inspecting the entire method body, including event bus postings (`NeoForge.EVENT_BUS.post(...)`), constructor arguments, method calls, and lambda closures.
