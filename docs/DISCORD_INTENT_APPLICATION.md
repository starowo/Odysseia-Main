# Odysseia-Main — Server Members Intent Review

Prepared against the live deployment on 9 September 2026.
Application ID: 1374372307916554351. Notice deadline: **10 September 2026**.

Hosting information updated on 13 September 2026 (UTC) after migration to OVHcloud.
This document is current reference text; editing it does not update a previously
submitted Discord application.

Only Server Members is requested. Message Content and Presence are disabled.
Publish and verify the completed privacy/evidence URLs before submission. The
owner must review the final accuracy/compliance acknowledgement.

## Selections

| Question | Answer |
| --- | --- |
| Server Members Intent | Request |
| Presence Intent | Do not request |
| Message Content Intent | Do not request |
| Public Privacy Policy | Yes once the completed policy is published |
| API Data stored off-platform | Yes |
| All API Data retained 30 days or less | No |
| Encryption at rest | Yes, LUKS2 encrypted application volume on the OVHcloud host |

## What does your application do?

Odysseia-Main manages membership and community administration for
类脑ΟΔΥΣΣΕΙΑ (Odysseia) and participating communities, including 类脑ΙΛΙΑΣ.
Features include rules-quiz verification, buffer/verified roles, automatic role
upgrades after the waiting period, synchronization of configured roles between
participating servers, forum-thread self-management and activity cleanup,
permission-restricted moderation, creator-license settings and community banner
applications. User-facing actions use slash commands, buttons and Discord
interactions.

The only privileged intent requested is Guild Members. Presence and Message
Content are disabled. Forum keyword filtering and legacy hand-written
punishment-history import are disabled. Bulk message deletion uses message IDs
and timestamps; its audit contains metadata rather than message bodies.
Anonymous-feedback and event modules are also disabled in this deployment.

Operator: SoliUmbra / starowo, for 类脑ΟΔΥΣΣΕΙΑ.
Source: https://github.com/starowo/Odysseia-Main

Privacy policy after publication:
https://github.com/starowo/Odysseia-Main/blob/main/docs/PRIVACY_POLICY.md

## Why do you need the Guild Members intent?

The bot handles member joins and member-role changes to maintain
administrator-configured role mappings between participating servers. A member
joining 类脑ΙΛΙΑΣ can automatically receive eligible roles held in
类脑ΟΔΥΣΣΕΙΑ. A role added or removed by a moderator in the source server can
be reflected in the configured counterpart server without requiring the member
to issue a command.

These join and role-update events occur independently of an interaction.
Fetching one known member when they click a button does not report the ongoing
changes that automatic synchronization needs. The manual synchronization panel
shown in the evidence is a user-facing recovery option, not the sole reason
for requesting this intent.

Verification automation also enumerates holders of configured buffer roles,
checks verification time (or join time as a fallback), and upgrades eligible
members after their waiting period. This scheduled processing operates without
requiring each affected member to interact with the bot. Authorized staff can
also transfer current holders of a configured role.

We use Discord IDs, roles, join/verification timestamps and necessary
verification/moderation state. We do not request online status, user activities
or ordinary server message bodies for these functions. Synchronization is
restricted to administrator-configured participating servers.

At inspection, the production configuration contained one two-server
synchronization group, with 194 source-server and 135 destination-server role
mappings. Live Discord audit records showed join-time synchronization,
role-change synchronization and automatic removal of buffer roles/addition of
the verified role. These are configuration counts at inspection, not user counts.

## Screenshots / videos

Publish this page and verify it is publicly accessible:
https://github.com/starowo/Odysseia-Main/blob/main/docs/review-20260909/README.md#guild-members

## API Data stored off-platform

Yes. The OVHcloud host stores SQLite/JSON operational records including
Discord IDs, verification results/timestamps and cooldowns, role mappings,
thread activity/mute/delegation state, moderation records and license/banner
settings. The bot does not collect presence data. Its current bulk-deletion audit
contains message metadata only. Content deliberately supplied through
interactions, such as moderation reasons, may still be stored.

## Retained for 30 days or less?

No. There is no universal automatic 30-day purge of all operational state.
Active sanctions, verification state and role mappings may remain longer.
The privacy policy discloses manual retention/deletion handling; it does not
promise a deletion schedule the application does not implement.

## How can users request deletion?

Contact SoliUmbra (Discord username: soliumbra) in 类脑ΟΔΥΣΣΕΙΑ or the
administrators of the relevant server, with the Discord ID and records
concerned. The existing operator support message is:
https://discord.com/channels/1134557553011998840/1338036166221365339/1400399975531282444

Non-confidential technical questions: https://github.com/starowo/Odysseia-Main/issues

The operator verifies and handles requests manually. Necessary active sanction
or safety records may need to be retained, with an explanation.

## Encryption at rest

Yes. Hosting is on an OVHcloud VPS in Oregon, United States. The application,
databases, configuration and logs reside on a LUKS2 encrypted volume. The unlock
key is held in a separate root-readable host file for unattended startup, with
a restricted operator recovery copy. This is encrypted application storage,
not a claim that the whole VPS system disk is encrypted or that root cannot
access the running application. SQLite/JSON do not add separate database-level
encryption. The previous GCP host retains a stopped rollback copy; migration
and recovery copies on the operator workstation have restricted access.
