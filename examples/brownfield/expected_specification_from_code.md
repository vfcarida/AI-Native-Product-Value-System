# Customer API Specification (Generated from Code Analysis)

## 1. Overview

API for managing customer data in AcmeBank systems.

**Version**: 2.3 (inferred from code)  
**Status**: Generated from code analysis  
**Last Updated**: 2026-11-18

## 2. Service Analysis

### 2.1 Methods

#### `getCustomer(String customerId)`

**Purpose**: Retrieve customer by ID

**Parameters**:
- `customerId` (String, required): Customer identifier

**Return**: `Optional<Customer>`

**Behavior**:
- Returns `Optional.empty()` if `customerId` is null or empty (after trim)
- Returns `Optional.empty()` if customer not found
- Returns `Optional.of(customer)` if found

**Edge Cases**:
- Null ID -> empty Optional
- Empty string -> empty Optional
- Whitespace-only -> empty Optional
- Non-existent ID -> empty Optional

**Issues Found**:
- No validation of ID format (FIXME comment in code)
- No logging (TODO comment in code)
- Documentation claims v2.3 update but lacks details

---

#### `createCustomer(String customerId, String name, String email)`

**Purpose**: Create new customer

**Parameters**:
- `customerId` (String, required): Customer identifier
- `name` (String, required): Customer name
- `email` (String, required): Customer email

**Return**: `Customer` object

**Validation Rules** (undocumented in old docs):
- Email MUST contain "@" character
- Throws `IllegalArgumentException` if email is null
- Throws `IllegalArgumentException` if email missing "@"

**Behavior**:
- **UNDOCUMENTED**: Duplicate customer IDs are silently overwritten
- No uniqueness check on email
- No format validation on name

**Issues Found**:
- Silent overwrite on duplicate ID (should error or update)
- Email validation too simplistic (accepts "user@" or "@domain")
- No validation on customerId or name (could be null or empty)
- References JIRA-1234 in comment (not accessible)

---

#### `updateCustomerEmail(String customerId, String newEmail)`

**Purpose**: Update customer's email address

**Parameters**:
- `customerId` (String, required): Customer identifier
- `newEmail` (String, required): New email address

**Return**: `boolean` (true if updated, false if customer not found)

**Issues Found**:
- **BUG**: Does not validate email format (inconsistent with `createCustomer`)
- Accepts invalid emails (null, empty, no "@")
- No audit trail for email changes

---

#### `deleteCustomer(String customerId)`

**Purpose**: Delete customer

**Parameters**:
- `customerId` (String, required): Customer identifier

**Return**: `void`

**Behavior**:
- Silently succeeds even if customer doesn't exist
- No confirmation or validation

**Issues Found**:
- **UNDOCUMENTED**: Method added in v2.1 but not in README
- No notification to dependent services (TODO in code)
- No audit logging (TODO in code)
- No soft-delete option

## 3. Drift Detection Report

### Code vs. Documentation Drift

| Element | In Code | In Docs | Status |
|---------|---------|---------|--------|
| `deleteCustomer` method | Exists (v2.1) | Missing | **DRIFT** |
| Email validation | `contains("@")` | Not specified | **DRIFT** |
| Duplicate ID behavior | Silently overwrites | Not specified | **DRIFT** |
| Null ID handling | Returns empty | Not specified | **DRIFT** |
| Version | 2.3 (inferred) | 1.0 (README) | **DRIFT** |

### Documentation Gaps

- No OpenAPI/Swagger specification
- No error response documentation
- No validation rules documented
- No authentication/authorization mentioned
- No rate limiting documented
- No examples in documentation

## 4. Characterization Tests (Recommended)

To preserve current behavior while enabling future refactoring:

```java
@Test
public void testGetCustomer_NullId_ReturnsEmpty() {
    Optional<Customer> result = service.getCustomer(null);
    assertFalse(result.isPresent());
}

@Test
public void testGetCustomer_EmptyId_ReturnsEmpty() {
    Optional<Customer> result = service.getCustomer("");
    assertFalse(result.isPresent());
}

@Test
public void testCreateCustomer_DuplicateId_Overwrites() {
    service.createCustomer("cust-1", "Alice", "alice@example.com");
    service.createCustomer("cust-1", "Bob", "bob@example.com");
    
    Optional<Customer> result = service.getCustomer("cust-1");
    assertEquals("Bob", result.get().getName());
}

@Test
public void testCreateCustomer_InvalidEmail_ThrowsException() {
    assertThrows(IllegalArgumentException.class, () -> {
        service.createCustomer("cust-2", "Charlie", "invalid-email");
    });
}

@Test
public void testUpdateCustomerEmail_NoValidation_AcceptsInvalidEmail() {
    service.createCustomer("cust-3", "Dave", "dave@example.com");
    boolean updated = service.updateCustomerEmail("cust-3", "not-an-email");
    
    // BUG: This should fail but succeeds
    assertTrue(updated);
}

@Test
public void testDeleteCustomer_NonExistent_SilentlySucceeds() {
    // Should this throw or return boolean? Current behavior: silent success
    service.deleteCustomer("does-not-exist");
}
```

## 5. Recommendations

### Immediate (Do Now)
1. **Update README** to version 2.3
2. **Document `deleteCustomer`** method (added in v2.1)
3. **Document validation rules** (email format, duplicate behavior)
4. **Add characterization tests** to preserve behavior

### Short-term (This Sprint)
1. **Fix email validation inconsistency** (updateCustomerEmail vs. createCustomer)
2. **Add ID format validation** (resolve FIXME)
3. **Add logging** (resolve TODO)
4. **Generate OpenAPI specification**

### Long-term (Next Quarter)
1. **Refactor duplicate ID behavior** (overwrite -> error)
2. **Add audit logging** for email changes and deletions
3. **Implement soft deletes**
4. **Add authentication/authorization**
5. **Achieve 70% test coverage**
