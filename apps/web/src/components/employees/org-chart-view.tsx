"use client";

import React, { useState } from "react";
import { ChevronDown, ChevronRight, GitBranch, Network, Users, XCircle } from "lucide-react";
import type { OrgChartNode } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useOrgChart } from "@/hooks/use-employees";
import { isOfflineError } from "@/lib/api-request";

interface OrgChartViewProps {
  onSelectEmployee: (employeeId: string) => void;
}

interface TreeNodeProps {
  node: OrgChartNode;
  depth: number;
  collapsedIds: Set<string>;
  onToggleCollapse: (id: string) => void;
  onSelectEmployee: (id: string) => void;
}

function collectParentIds(nodes: OrgChartNode[], out: Set<string>): void {
  for (const node of nodes) {
    const children = node.children ?? [];
    if (children.length > 0) {
      out.add(node.id);
      collectParentIds(children, out);
    }
  }
}

function OrgTreeNode({
  node,
  depth,
  collapsedIds,
  onToggleCollapse,
  onSelectEmployee,
}: TreeNodeProps) {
  const children = node.children ?? [];
  const hasChildren = children.length > 0;
  const isCollapsed = collapsedIds.has(node.id);

  return (
    <li className="space-y-2">
      <div
        className={`flex flex-col sm:flex-row sm:items-center justify-between gap-2 rounded-small border border-appBorder bg-white p-3 transition-colors hover:border-[#2A5BDB]/50 hover:bg-[#F8F9FD] ${
          depth === 0 ? "border-l-4 border-l-[#2A5BDB]" : ""
        }`}
      >
        <div className="flex items-center gap-2.5 min-w-0">
          {hasChildren ? (
            <button
              type="button"
              onClick={() => onToggleCollapse(node.id)}
              aria-expanded={!isCollapsed}
              aria-label={
                isCollapsed
                  ? `Expand direct reports for ${node.full_name}`
                  : `Collapse direct reports for ${node.full_name}`
              }
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded border border-appBorder bg-[#F8F9FD] text-[#102C54] hover:bg-slate-200/70"
            >
              {isCollapsed ? (
                <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          ) : (
            <span className="inline-block h-6 w-6 shrink-0" aria-hidden="true" />
          )}

          <button
            type="button"
            onClick={() => onSelectEmployee(node.id)}
            className="text-left min-w-0 group"
          >
            <span className="block text-xs font-bold text-[#102C54] group-hover:text-[#2A5BDB] truncate">
              {node.full_name}
            </span>
            <span className="block text-[11px] text-textSecondary truncate">
              {node.job_title} • <span className="font-mono">{node.email}</span>
            </span>
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 shrink-0">
          {node.department_name && (
            <Badge variant="outline" className="text-[10px]">
              {node.department_name}
            </Badge>
          )}
          <Badge variant={node.status === "active" ? "success" : "warning"} className="text-[10px]">
            {node.status.replace("_", " ")}
          </Badge>
          <Badge variant="primary" className="text-[10px]">
            <Users className="mr-1 h-3 w-3" aria-hidden="true" />
            {node.direct_reports_count}{" "}
            {node.direct_reports_count === 1 ? "direct report" : "direct reports"}
          </Badge>
        </div>
      </div>

      {hasChildren && !isCollapsed && (
        <ul className="ml-6 space-y-2 border-l-2 border-slate-200 pl-4">
          {children.map((child) => (
            <OrgTreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              collapsedIds={collapsedIds}
              onToggleCollapse={onToggleCollapse}
              onSelectEmployee={onSelectEmployee}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export function OrgChartView({ onSelectEmployee }: OrgChartViewProps) {
  const { isStandby } = useBackendStatus();
  const { data, isLoading, error, refetch, isFetching } = useOrgChart(true);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => new Set());

  const isOffline = isStandby || isOfflineError(error);
  const liveHttpError = error && !isOfflineError(error) ? error.message : null;

  const handleToggleCollapse = (id: string) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleCollapseAll = () => {
    if (!data) return;
    const allParents = new Set<string>();
    collectParentIds(data.roots, allParents);
    setCollapsedIds(allParents);
  };

  const handleExpandAll = () => {
    setCollapsedIds(new Set());
  };

  if (isOffline && !data) {
    return (
      <StandbyBanner
        title="Organization Chart Unavailable in Standby"
        message="Connect FastAPI to load the live PostgreSQL organizational hierarchy."
        onRetry={() => void refetch()}
        isRetrying={isFetching}
      />
    );
  }

  if (liveHttpError) {
    return (
      <div
        role="alert"
        className="flex items-center justify-between rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
      >
        <div className="flex items-center gap-2">
          <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
          <span className="font-medium">{liveHttpError}</span>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-medium bg-[#2A5BDB]/10 text-[#2A5BDB]">
                <Network className="h-4 w-4" aria-hidden="true" />
              </div>
              <div>
                <CardTitle className="text-base text-[#102C54]">
                  Reporting Hierarchy ({data?.total_employees ?? 0} Employees)
                </CardTitle>
                <CardDescription className="text-xs">
                  Cycle-safe organization tree sourced from public.pub_employees. Click any person
                  to open their profile drawer.
                </CardDescription>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button type="button" variant="outline" size="sm" onClick={handleExpandAll}>
                Expand All
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={handleCollapseAll}>
                Collapse All
              </Button>
            </div>
          </div>
        </CardHeader>

        <CardContent>
          {isLoading ? (
            <div className="space-y-3 animate-pulse" aria-busy="true">
              <div className="h-14 rounded-small bg-slate-100" />
              <div className="ml-6 h-14 rounded-small bg-slate-100" />
              <div className="ml-12 h-14 rounded-small bg-slate-100" />
            </div>
          ) : !data || (data.roots.length === 0 && data.unassigned.length === 0) ? (
            <div className="rounded-small border border-dashed border-appBorder bg-[#F8F9FD] p-6 text-center text-xs text-textSecondary">
              No organizational hierarchy available yet. Add employees or assign managers in Step 4.
            </div>
          ) : (
            <div className="space-y-6">
              {data.roots.length > 0 && (
                <ul className="space-y-3">
                  {data.roots.map((root) => (
                    <OrgTreeNode
                      key={root.id}
                      node={root}
                      depth={0}
                      collapsedIds={collapsedIds}
                      onToggleCollapse={handleToggleCollapse}
                      onSelectEmployee={onSelectEmployee}
                    />
                  ))}
                </ul>
              )}

              {data.unassigned.length > 0 && (
                <div className="space-y-3 border-t border-appBorder pt-5">
                  <div className="flex items-center gap-2">
                    <GitBranch className="h-4 w-4 text-amber-600" aria-hidden="true" />
                    <h3 className="text-sm font-bold text-[#102C54]">
                      Unassigned / No Manager ({data.unassigned.length})
                    </h3>
                  </div>
                  <ul className="space-y-2">
                    {data.unassigned.map((orphan) => (
                      <OrgTreeNode
                        key={orphan.id}
                        node={orphan}
                        depth={0}
                        collapsedIds={collapsedIds}
                        onToggleCollapse={handleToggleCollapse}
                        onSelectEmployee={onSelectEmployee}
                      />
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
