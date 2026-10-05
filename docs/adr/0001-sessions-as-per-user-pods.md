# 0001: Sessions as per-user pods

## Context

Interactive tools need stable private authentication state, accountable access and independently bounded resources. Team membership must not expose another person's seat.

## Decision

Use one namespace per user and a StatefulSet/login claim per user, tool and home node. Holders may inspect, attach and scale their approved workloads; GitOps controls pod specifications. Workspaces remain ephemeral and export as patches for review.

## Consequences

RBAC, quotas, policy and deletion align with the individual boundary. Onboarding generates more objects and home-node capacity needs planning. Offboarded rendering does not erase retained storage. Node-root and cluster-root remain trusted.
