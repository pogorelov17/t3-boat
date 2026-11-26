## Generic workers

This installation has no role playbook. No roles, reviews or fixed sequence are required: you decide what to delegate, to how many workers, and in what order.

1. Start each worker with `boat-worker start`. It opens with the `lead` row's model and has every provider of this profile enabled, so `delegate_task` may select any model and option that `orchestrator_capabilities` lists for its environment. Name the model you chose and why in the parent update. Never choose max effort automatically.
2. Write one self-contained brief per worker. The worker knows only its brief and the repository: give the goal, the repository URL, the base or target branch, the scope, what is already known, constraints, the deliverable (for example a draft PR with passing tests, or a report with file and line evidence or source links and dates) and how success is checked.
3. Any task fits a worker: inspection, implementation, tests, research or a review of a given SHA. The brief decides. A worker may split its own work further when the brief allows it and its tools support it; ask it to name in its result what it handed on and what came back.
4. Ask for an independent review on a fresh VM when the owner wants one or the risk calls for it, and give that worker the PR URL and exact head SHA.
5. The rows in the table below are model presets from the profile. Use `boat-worker start ROLE` when a preset fits the task.
