"use client";

import { useQuery } from "@tanstack/react-query";
import type { Employee } from "@agentic-hr/contracts";
import { ApiHttpError, apiRequest } from "@/lib/api-request";
import { API_BASE_URL } from "@/lib/platform";

export const STANDBY_SAMPLE_EMPLOYEES: Employee[] = [
  {
    employee_id: "EMP-001",
    name: "Ubaid Qureshi (Owner)",
    email: "agentic.hr.test.ubaid.owner@gmail.com",
    department: "Executive",
    role: "Company Owner & Admin",
    status: "Active",
    is_test_account: true,
  },
  {
    employee_id: "EMP-002",
    name: "Sarah Jenkins (Recruiter)",
    email: "agentic.hr.test.ubaid.recruiter@gmail.com",
    department: "HR",
    role: "Talent Acquisition Lead",
    status: "Active",
    is_test_account: true,
  },
  {
    employee_id: "EMP-003",
    name: "Tariq Mahmood (Hiring Manager)",
    email: "agentic.hr.test.ubaid.hm@gmail.com",
    department: "Engineering",
    role: "VP of Engineering",
    status: "Active",
    is_test_account: true,
  },
  {
    employee_id: "EMP-004",
    name: "Zainab Ali (Employee)",
    email: "agentic.hr.test.ubaid.employee@gmail.com",
    department: "Engineering",
    role: "Senior Full-Stack Engineer",
    status: "Active",
    is_test_account: true,
  },
];

export interface EmployeesQueryResult {
  employees: Employee[];
  source: "live" | "standby";
}

export function useEmployees(tenantSlug = "acme-tech-01") {
  return useQuery<EmployeesQueryResult, Error>({
    queryKey: ["employees", tenantSlug],
    queryFn: async () => {
      const res = await apiRequest<Employee[]>(
        `${API_BASE_URL}/api/synthetic/employees`,
        {
          cache: "no-store",
          headers: { "X-Tenant-ID": tenantSlug },
        },
        "Failed to load employees"
      );

      if (res.kind === "live") {
        return { employees: res.data, source: "live" };
      }
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      return { employees: STANDBY_SAMPLE_EMPLOYEES, source: "standby" };
    },
    staleTime: 1000 * 30,
    retry: 1,
  });
}
