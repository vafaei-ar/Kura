# CI and manual release — 8 September 2026

Merging code and deploying it are separate actions. These workflow changes take
effect on main only when the feature branch containing them is merged. Until
then, the older workflow still present on main retains its old behavior.

## Checks without deployment

`.github/workflows/ci.yml` runs on pull requests targeting main and pushes to
main or feat/**, and can be reused by the manual release workflow. It uses a
read-only GitHub token, does not receive Azure secrets, and has no deployment
steps. Kura checks include backend tests, console syntax, Swift recovery/ring
tests and an unsigned iOS simulator build. VERA checks include backend tests and
web-client syntax. These do not replace the paired-process or phone acceptance
tests.

GitHub branch protection/rulesets have not been changed. A repository owner
should require the normal CI job checks before merging; merely defining CI does
not make those checks mandatory. No pull_request_target trigger or inherited
deployment secrets are used.

## Explicit deployment only

The workflow `deploy-push-service.yml` is named **Deploy Kura manually**. It has
only a workflow_dispatch trigger, not a push, PR, or workflow_run trigger.

1. Review both backend release SHAs, compatibility, runtime configuration,
   enrollment readiness, persistent storage, and backup/restore procedure.
   Clinical/content approval is still required for real patient use. Do not mark
   draft policy approved just to satisfy a startup gate.
2. Run the paired synthetic integration against the intended revisions; complete
   the agreed phone/operations checks before a patient pilot. Disable external
   notifications/recording unless explicitly approved and configured.
3. Drain active check-ins before an incompatible engine change. Schedule both
   backend updates together; these are two separate jobs, not an atomic release.
4. In Actions, select **Deploy Kura manually** → **Run workflow**. Select
   main, supply the full 40-character SHA that main resolves to for this run, and
   type exactly `DEPLOY KURA`.
5. The authorization job rejects any other branch, SHA, confirmation, or event.
   The same CI workflow must pass before deployment. The deploy job repeats the
   gate and checks out github.sha, not a moving branch or arbitrary input ref.
   Azure credentials are accessed only in that deploy job.
6. Verify health, authenticated enrollment, invitation, durable answer receipt,
   outcome delivery, and operator visibility on the actual deployment.

Requests serialize per repository and do not cancel an in-flight deployment.
The package contains tracked source only; runtime data/outcomes, test folders and
.env are excluded. Backups and persistent volumes remain essential: package
exclusions alone are not a database backup or a guarantee of hosting persistence.

This is an explicit operator-confirmation gate, not an independent reviewer
approval or clinical approval. GitHub required-reviewer environments were not
created/configured. Existing Azure credential names and VERA OIDC branch identity
are preserved. Adding a deployment environment later requires reviewing its
protection rules and, for VERA, the matching Azure federated identity subject.

## Rollback and verification

Do not force-push main or restore older code over an active incompatible dialog.
Prepare a reviewed revert commit on main, preserve additive database fields and
outcome files, drain active sessions, rerun checks and use the same manual gate.
A mismatched old SHA cannot be deployed by entering it against a newer main run.

Local workflow validation checks YAML/actions syntax and authorization cases:
approved request, push, PR, feature branch, wrong/empty SHA, missing confirmation,
and literal shell-injection text. Validation never dispatches the deploy workflow.
Remote CI results must be checked separately; local validation is not proof of
GitHub-runner or Azure success.

References: [GitHub manual workflow inputs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
and [reusing the same workflow](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).
