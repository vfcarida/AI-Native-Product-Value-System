# Customer API

## Overview

The Customer API provides basic CRUD operations for customer data.

**Version**: 1.0 (Last updated: 2023-04-15)

## Endpoints

### GET /api/customers/{id}
Returns customer by ID.

**Response**: 200 OK with customer JSON

### POST /api/customers
Creates new customer.

**Request Body**:
```json
{
  "id": "cust-123",
  "name": "John Doe",
  "email": "john@example.com"
}
```

**Response**: 201 Created

## Known Issues

- None reported

## Contact

Email: legacy-team@acmebank.com (team no longer exists)
