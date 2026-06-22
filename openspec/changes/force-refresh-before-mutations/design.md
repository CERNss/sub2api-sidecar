## Approach
Add a reusable `OperationalDataRefresher` around the existing multi-upstream collector. It performs a raw collection, then refreshes usage segmentation and group usage from the new snapshots. Mutation-capable services receive this refresher by dependency injection.

## Mutation Boundaries
Rotation and key workflows refresh before real execution paths:
- manual user rotation
- existing user/group orchestration
- group migration
- single API-key group update
- encoded key transfer execution
- automatic rotation execution
- automatic run rollback
- token API key creation

Credit-control workflows refresh before:
- manual explicit-user adjustment
- manual filter adjustment, before resolving filter targets
- run-policy-now
- scheduler due-policy execution

Previews and dry-runs do not refresh because they do not write Sub2API state.

## Failure Behavior
The forced refresh treats any collector error message or derived refresh exception as blocking. API calls surface this as a 502 response; schedulers record the failure through their existing tick error snapshots.
