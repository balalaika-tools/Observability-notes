# PDMA Service Identity (repository-specific)

**Do not open this file unless the service you are instrumenting belongs to the
PDMA repository.** Nothing in it generalises; adopting these values for another
service produces telemetry filed under the wrong system.

Everything else about resource identity — the contract, ownership, version
policy, runtime routing — is in `../setup/resource_identity.md` and applies to
every service.

## Mapping

```text
service.namespace = product-data-management-automation
service.name      = pdma-api | pdma-worker
service.version   = full Git commit SHA supplied by CI/build
```

`pdma-api` and `pdma-worker` share the namespace and, when built from the same
commit, the same `service.version`. They stay distinguishable because
`service.name` differs — that is the whole reason the triplet is
namespace + name + instance rather than name alone.

Each replica receives `service.instance.id` from the deployment platform using
the runtime rules in `../setup/resource_identity.md`; no PDMA-specific override
applies.

## Adding another repository

Copy this file, replace the mapping, and route it the same way: one line in
`SKILL.md`'s repository-specific row, loaded only when the target repository
matches. Never move a concrete namespace back into the shared references.
