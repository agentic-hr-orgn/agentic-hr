/**
 * @agentic-hr/contracts
 * Shared TypeScript domain models, API response DTOs, and schemas.
 */

import type { components } from "./api-client";

// Derived API DTOs directly from OpenAPI schema (no manual duplication)
export type HealthResponse = components["schemas"]["HealthResponse"];
export type EmployeeRecord = components["schemas"]["EmployeeRecord"];

export interface TenantContext {
  tenant_id: string;
  user_id?: string;
  role?: string;
}

export interface Employee {
  employee_id: string;
  name: string;
  email: string;
  department: string;
  role: string;
  status: string;
  is_test_account: boolean;
}

export interface PolicyDocument {
  title: string;
  language: "en" | "ur" | string;
  category: string;
  summary: string;
}

export interface CompanyProfile {
  name: string;
  domain: string;
  departments: string[];
  total_employees: number;
  test_accounts: string[];
  policy_documents: PolicyDocument[];
}

export type AgentRole = "DocuVerify" | "Screener" | "Code Tester" | "Policy Brain";

export interface AgentStatus {
  id: string;
  role: AgentRole;
  name: string;
  model: string;
  status: "idle" | "running" | "completed" | "error";
  lastRun?: string;
}

// Type-safe OpenAPI client and factory
export * from "./client";

// Auto-generated OpenAPI Schema Contracts
export type * from "./api-client";
