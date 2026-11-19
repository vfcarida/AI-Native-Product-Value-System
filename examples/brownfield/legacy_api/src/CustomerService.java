package com.acmebank.customer;

import org.springframework.stereotype.Service;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Customer service for managing customer data.
 * 
 * @deprecated This class needs documentation update
 * @author Legacy Team (2023)
 */
@Service
public class CustomerService {
    
    private Map<String, Customer> customers = new HashMap<>();
    
    /**
     * Get customer by ID.
     * 
     * Note: This method was updated in v2.3 to handle null IDs,
     * but documentation was never updated.
     */
    public Optional<Customer> getCustomer(String customerId) {
        if (customerId == null || customerId.trim().isEmpty()) {
            return Optional.empty();
        }
        
        // FIXME: This should check for valid ID format
        // TODO: Add logging
        
        Customer customer = customers.get(customerId);
        return Optional.ofNullable(customer);
    }
    
    /**
     * Create new customer.
     * 
     * WARNING: This method has undocumented validation rules.
     * See JIRA-1234 for details.
     */
    public Customer createCustomer(String customerId, String name, String email) {
        // Undocumented validation: email must contain @
        if (email == null || !email.contains("@")) {
            throw new IllegalArgumentException("Invalid email");
        }
        
        // Undocumented behavior: duplicate IDs are silently overwritten
        Customer customer = new Customer(customerId, name, email);
        customers.put(customerId, customer);
        
        return customer;
    }
    
    /**
     * Update customer email.
     * 
     * @param customerId Customer ID
     * @param newEmail New email address
     * @return true if updated, false if customer not found
     */
    public boolean updateCustomerEmail(String customerId, String newEmail) {
        Customer customer = customers.get(customerId);
        if (customer == null) {
            return false;
        }
        
        // BUG: This doesn't validate email format (should match createCustomer)
        customer.setEmail(newEmail);
        return true;
    }
    
    /**
     * Delete customer.
     * 
     * Note: Added in v2.1. No documentation exists.
     */
    public void deleteCustomer(String customerId) {
        customers.remove(customerId);
        // TODO: Should we notify other services?
        // TODO: Audit log?
    }
}

class Customer {
    private String id;
    private String name;
    private String email;
    
    public Customer(String id, String name, String email) {
        this.id = id;
        this.name = name;
        this.email = email;
    }
    
    public void setEmail(String email) {
        this.email = email;
    }
}
