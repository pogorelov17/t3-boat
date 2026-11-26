---
name: t3-boat
description: Run T3 project work on disposable Boat worker VMs from briefs the team lead writes, and post compact factual updates in the parent chat.
---

# Boat project workflow

The parent chat is the team lead. Discuss ideas, requirements and acceptance criteria in the parent chat. A planning conversation needs only the team lead. Use the user's language. Start this workflow when a project needs repository inspection, edits, project commands, tests or extended research.

## Machine boundary

- **Team lead, on this T3 server:** discuss, clarify, plan, decide and report. Targeted web fact checks are fine here: a few searches or pages to confirm one fact, cited with source and date. Delegate extended research to a Boat worker.
- **Boat worker VM:** all source inspection, edits, project commands, tests, dependency installs, builds and clones. Nothing from a repository is cloned, fetched, installed or run on this server. The project folder here is an empty Git stub that holds only the origin URL and the `boat.environment` setting.
- **Control plane, on this T3 server:** `boat-worker start`, `orchestrator_capabilities`, `delegate_task`, `t3 environment unlink`, `boat delete` and `boat deletion status`, plus the parent updates below.

## Start a worker

1. Check the current thread provider, model and effort against the lead model at the end of this skill. If a project override or an unavailable option changes the selection, tell the owner what to select before starting work. Never silently substitute.
2. From the current project's empty Git stub, read the origin URL and the local `boat.environment` setting. For work in an unbound workspace, guide the owner to create a GitHub repository with a README or choose an existing repository, then attach it using `t3-boat setup`. Resume the plan in the bound workspace.
3. Run `boat-worker start` from this stub, as **Workers and briefs** or **Attached playbook** below describes. It provisions one fresh VM, opens T3 there with the lead model, enables every provider of this profile and links the VM as a T3 environment. Record the returned VM and linked environment IDs.
4. Check `orchestrator_capabilities` for the linked environment, then use T3's native `delegate_task` with that environment and the provider instance, exact model and options you chose for this worker. If the option or model is missing, report it and release the VM. Require each child to confirm the model and option when visible. Distinguish the launch setting from anything independently echoed by the provider.
5. The brief is the worker's whole task: it knows only its brief, its instruction file and the repository. State in it what the worker delivers and in what form, as agreed with the owner or as the attached playbook defines; T3 Boat sets no deliverable, branch or review rule of its own. Boat environments can pre-clone the project under the worker's home, so tell the worker to verify the checkout's origin against the requested repository, use that checkout if it matches, or clone on the VM if absent.
6. Verify what a worker reports before relying on it, such as the commits, branches, links and test results it names.

## Parent updates

Post a compact update in the parent chat whenever you delegate, a result arrives, or you decide something. Use this shape, one short line per field, and leave out a field that has nothing observed:

- **Who → whom:** for example `lead → worker on bx_… (child link)`, with provider, model and option as launched.
- **Asked:** the task in one line.
- **Depends on:** dependencies you actually know, such as a commit or another worker's result.
- **Result:** what the child reported or you verified, with test commands and pass/fail and the identifiers of what it delivered.
- **Decision:** what you decided and why, or the question for the owner with a recommendation.

These updates take the same form with or without a playbook, whatever a playbook or brief says. State only observed facts. Mark anything still pending or unconfirmed as such. Link the child thread or what it delivered for detail instead of pasting shell output, logs or transcripts. T3 shows this chat a child's link and the result it returns. It does not stream a child's progress here, and work a child hands on to other agents is not visible here. Do not describe live progress or nested activity unless a child's returned result reports it, and then attribute it to that child.

## Cleanup

Copy each child's outcome (or failure) into the parent before unlinking the environment and deleting its VM. Run `t3 environment unlink ENVIRONMENT_ID` and `boat delete --yes VM_ID` using the exact IDs created for that worker. Use the returned deletion operation ID with `boat deletion status ID --json` to confirm completion. Report any pending cleanup, including failures and cancelled work. Never snapshot or reuse workers. Keep credentials, pairing codes, account information and private reasoning out of messages. On interruption, resume from the recorded IDs and the last verified result.
