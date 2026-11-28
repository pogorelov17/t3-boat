---
name: t3-boat
description: Run T3 project work on disposable Boat worker VMs from briefs the team lead writes, and post compact factual updates in the parent chat.
---

# Boat project workflow

The parent chat is the team lead. Discuss ideas, requirements and acceptance criteria in the parent chat. A planning conversation needs only the team lead. Use the user's language. Start this workflow when a project needs repository inspection, edits, project commands, tests or extended research.

## Machine boundary

- **Team lead, on this T3 server:** discuss, clarify, plan, decide and report. Targeted web fact checks are fine here: a few searches or pages to confirm one fact, cited with source and date. Delegate extended research to a Boat worker.
- **Boat worker VM:** all source inspection, edits, project commands, tests, dependency installs, builds and clones. Nothing from a repository is cloned, fetched, installed or run on this server. The project folder here is an empty Git stub that holds only the origin URL and the `boat.environment` setting.
- **Control plane, on this T3 server:** `boat-worker start`, `t3-boat roles`, `t3_project_list`, `orchestrator_capabilities`, `delegate_task`, `t3 environment unlink`, `boat delete` and `boat deletion status`, plus the parent updates below.

## Runtime role models

When the generated **Attached playbook** section contains a model-assignment table, treat it as the effective assignment for future workers. `owner override` takes precedence over the selected model set. Run `t3-boat roles` to show current assignments and `t3-boat roles models` to show the live selectable catalog before proposing a change.

An explicit owner request may choose another live model for one delegation without saving it. For a persistent change, first show the current assignment and the exact proposed diff. Only after explicit owner approval run `t3-boat roles set ROLE --provider PROVIDER (--family FAMILY | --model MODEL) --effort EFFORT`; use `t3-boat roles reset ROLE` to restore the selected model set. Never persist a change autonomously, never use this command to change the lead model and never claim it changed a worker that is already running. A successful command applies to future workers without an image rebuild or container restart.

## Start a worker

1. Check the current thread provider, model and effort against the lead model at the end of this skill. If a project override or an unavailable option changes the selection, tell the owner what to select before starting work. Never silently substitute.
2. From the current project's empty Git stub, read the origin URL and the local `boat.environment` setting. For work in an unbound workspace, guide the owner to create a GitHub repository with a README or choose an existing repository, then attach it using `t3-boat setup`. Resume the plan in the bound workspace.
3. Run `boat-worker start` from this stub, as **Workers and briefs** or **Attached playbook** below describes. It provisions one fresh VM, opens T3 there with the lead model, enables every provider of this profile and links the VM as a T3 environment. Record the returned VM ID, the linked environment ID and the **worker T3 project folder** it prints.
4. Resolve the worker's project on the linked environment: call `t3_project_list` with `environmentId` set to that environment ID and take the `id` of the project whose `workspaceRoot` is the printed worker project folder. T3 on the VM registers that scratch folder as its project; the repository checkout can be in another folder, so never derive the project from the repository name, from the checkout path or from this server's project. If no listed project has that folder, report it and release the VM.
5. Check `orchestrator_capabilities` for the linked environment and resolve the effective role assignment to a listed exact model. Then use T3's native `delegate_task` with a `target` holding the linked `environmentId`, the resolved `projectId`, and the `providerInstanceId`, exact `model` and `options` chosen from that assignment. If the option or model is missing, report it and release the VM. Require each child to confirm the model and option when visible. Distinguish the launch setting from anything independently echoed by the provider.
6. The brief is the worker's whole task: it knows only its brief, its instruction file and the repository. State in it what the worker delivers and in what form, as agreed with the owner or as the attached playbook defines; T3 Boat sets no deliverable, branch or review rule of its own. The child starts in the scratch project folder, and Boat environments can pre-clone the project elsewhere under the worker's home, so tell the worker to verify the checkout's origin against the requested repository, use that checkout if it matches, or clone on the VM if absent.
7. Verify what a worker reports before relying on it, such as the commits, branches, links and test results it names.

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
