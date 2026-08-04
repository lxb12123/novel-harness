# Workbench Product Language Design

## Goal

Keep the existing workbench layout while making it read like a finished writing product rather than an experiment dashboard.

## Product boundary

Internal identifiers and evaluation concepts may remain in TypeScript types, API payloads, comments, and tests. They must never appear in user-visible copy. This includes `R1`–`R4`, `M2`, ADR numbers, `valid_from`, `must_not_reveal`, `CANON`, `PROVISIONAL`, `X0`–`X2`, kill-gate language, raw rule names, and raw run statuses.

## Rules

1. The chapter control only contains chapters returned by `/chapters`. Authors cannot navigate to an unwritten chapter by typing a number.
2. Production placeholders contain instructions, not fixture characters, places, secrets, or plot events.
3. Unavailable product capabilities are hidden instead of shown as permanently disabled experiment controls.
4. Blank projects show one clear next action. Empty technical panels and empty timelines do not compete for attention.
5. Advanced data is translated into author language: “本场不能说破”, “本章尚未登场”, “检查本章”, “原文依据”, and “待确认内容”.
6. AI drafting keeps the production path only. Experiment arms, evaluation warnings, model diagnostics, and test metadata remain internal.

## Empty workbench

The chapter list and editor remain visible because the blank-book bootstrap creates a real first chapter. An empty roster shows a short “添加第一个条目” action. The right panel shows a single explanation until a roster entry exists, and the bottom timeline stays hidden until a scene or selected character gives it content.

## Verification

Component tests cover constrained chapter selection, absence of demo placeholders and internal terminology, translated panel copy, and blank-project empty states. A source guard prevents the main set of internal product terms from returning in user-visible production strings.
