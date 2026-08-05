# Discord Privileged Intent Application — Odysseia

Replace all `[bracketed values]` and attach public screenshots/video links.

## Selections

- Public Privacy Policy: **Yes** — `[PUBLIC_PRIVACY_POLICY_URL]`
- Server Members Intent: **Apply**
- Presence Intent: **Do not apply**
- Message Content Intent: **Apply**

## Application Details

Odysseia is a multi-server community administration bot operated by
`[OPERATOR/COMMUNITY]`. It primarily uses slash commands and Discord UI
components. Features include member verification and role assignment,
cross-server role synchronization, forum/thread self-management and cleanup,
moderator-requested message cleanup and evidence review, anonymous feedback,
forum post filtering, event participation checks, content-license workflows and
moderation tools.

Commands are permission-gated. Data is used only to deliver these features and
prevent abuse; it is not sold, and message content is not used to train AI/ML
models. Privacy policy: `[PUBLIC_PRIVACY_POLICY_URL]`. Demo server:
`[SERVER_INVITE_URL]`.

## Why do you need the Guild Members intent?

Guild Members is required for member lifecycle and role-based administration
that interaction payloads alone cannot provide. Odysseia listens for joins and
member updates to apply verification and cross-server role synchronization.
Authorized staff can transfer all current holders of one role to another. Event
managers can compare current members of a selected role with forum authors.
Verification automation promotes eligible members from configured buffer roles
after a waiting period.

Only member ID, join timestamp and role IDs needed for these features are read.
The bot does not use presence/activity data. Bulk operations are staff-only.

Demo: `[GUILD_MEMBERS_DEMO_URL]`

### Are you storing any API Data off-platform?

**Yes.** Restricted SQLite/JSON storage contains Discord IDs and limited
verification, role mapping, thread activity, moderation/safety and settings
records. It contains no presence data. Retention is disclosed in the policy.

## Presence Intent

Do not select it. Code forces `intents.presences = False`; no feature reads user
status, client status, activities or custom status.

## Message Content answers

- Opt out? **Yes for optional features.** Users choose whether to invoke feedback
  and license workflows; forum welcome tracking has opt-out. Admin-enabled safety
  filtering/moderation cannot be individually opted out of while participating
  in that server, as disclosed in the privacy policy.
- Stored off-platform? **Yes.** Deliberately submitted anonymous feedback and
  attachment URLs are stored. Routine thread scanning stores only IDs, counts
  and timestamps. Moderator-created evidence/backup records may contain selected
  message text.
- Used to train AI/ML? **No.**

## Why do you need the Message Content intent?

Odysseia needs Message Content for narrowly scoped, administrator-enabled
features that inspect existing or new messages. The forum filter checks opening
and subsequent posts against a configured word list. Thread cleanup reads
history to calculate activity but retains only counts/timestamps. Authorized
moderation commands read selected ranges for deletion or a requested backup.
The license workflow detects its own helper messages in configured forums.
Anonymous feedback listens in DM only after a user explicitly starts an
image/file upload, then consumes that user's next message and attachment.

The bot ignores bot messages and scopes listeners to relevant DMs, threads or
configured channels. Content is not used for ads, profiling, sale or AI training.

Demo: `[MESSAGE_CONTENT_DEMO_URL]`

## Demo recording checklist

Show: the server and bot profile; join/verification and a synced role update;
the role-member/event check; forum filtering and thread activity cleanup; the
explicit feedback DM upload; admin permission checks and unauthorized rejection;
the module list, public privacy URL, and Presence disabled in the Portal. Use a
reviewer-accessible public/unlisted link that requires no access request.
