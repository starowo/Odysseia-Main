# Odysseia-Main Privacy Policy

Last updated: 9 September 2026

This policy describes Odysseia-Main (Discord application ID 1374372307916554351),
maintained by **SoliUmbra / starowo for the 类脑ΟΔΥΣΣΕΙΑ community** and
participating communities. Independently hosted copies of this open-source
software have their own operators and practices.

## Contact and data requests

Contact **SoliUmbra (Discord username: soliumbra)** in 类脑ΟΔΥΣΣΕΙΑ for privacy
questions, access, correction or deletion. The operator's existing support
message is in the [new-member verification channel](https://discord.com/channels/1134557553011998840/1338036166221365339/1400399975531282444).
You may also contact the administrators of the server where you use the bot.
For non-confidential technical questions, use the
[project issue tracker](https://github.com/starowo/Odysseia-Main/issues).
Do not post private messages, credentials or sensitive evidence in public issues.

## Data the bot uses

The bot uses Discord user, server, role, channel, thread, message and interaction
IDs; member roles and join times; and information supplied to its commands.
These support verification, role assignment and synchronization, thread
management, moderation, creator-license settings and banner applications.

Verification records include user/server IDs, attempt times and results,
successful-verification time, cooldowns and temporary question state. Thread
records include user/thread IDs, message counts or activity timestamps,
delegation, mute state and welcome-message preferences. Moderation records can
include the affected account, staff account, reason, sanction times, evidence
references and administrative decisions. Role mappings, license preferences and
banner applications are stored when needed by their features.

Message Content is disabled. The bot does not read ordinary server message
bodies for keyword filtering or moderation transcripts. Bulk cleanup operates
on message IDs and timestamps, and its audit records message ID, author ID and
creation time only. Routine thread activity tracking stores IDs and statistics,
not a copy of every conversation. Forum keyword filtering and legacy
hand-written punishment-history import are disabled.

Discord still provides content deliberately supplied through interactions,
direct messages to the bot and the bot's own messages without this privileged
intent. Such inputs, including moderator-provided reasons, may be used or stored
for the requested feature. Disabling the intent does not mean the bot receives
no user-provided text at all.

The hosted application's anonymous-feedback and event modules are currently
disabled. Presence is disabled: the bot does not track members' online status,
custom status, games or activities. Displaying the bot's own status does not
mean that it tracks users' presence.

## Purposes, hosting and access

Data is used to operate requested community features, maintain role-based
access, prevent abuse and allow authorized staff to review moderation actions.
The bot uses Discord APIs and is currently hosted on Google Cloud Platform in
Oregon, United States (us-west1). Operational records are stored in SQLite/JSON
files and application logs. Google Cloud and Discord process data as the
infrastructure providers for these services.

The Google Compute Engine disks provide
[encryption at rest](https://docs.cloud.google.com/compute/docs/disks/disk-encryption).
This is infrastructure encryption; the SQLite/JSON files do not add a separate
application-level encryption layer.

Administrative commands use configured Discord role and staff checks. Host
access is restricted to the operator and authorized system administrators.
Audit attachments are sent to the server's configured moderation-log
destination; administrators must restrict its visibility appropriately.

The bot does not use Discord message content to train ML/AI models, send it to
an AI inference service, sell it or use it for advertising. The subject matter
of a community does not change these limits on this bot's data use.

## Storage and retention

The bot does not continuously archive server messages. Current moderation audit
attachments contain metadata only and are uploaded to the configured Discord
moderation log, where they remain until authorized staff delete them.
Operational records, logs and historical records from earlier versions may
remain until an administrator removes them. Turning off Message Content does
not delete pre-existing records or content deliberately supplied to commands.

There is currently no universal automatic 30-day or 180-day deletion rule for
all stored records. Verification, sanctions and role-related state may be needed
after an interaction ends. This policy does not promise an automatic deadline
the application does not implement. The operator handles deletion requests
manually and evaluates what is still needed for active restrictions, abuse
prevention or moderation appeals.

## Choices and deletion

Users can avoid optional license/banner features, and forum welcome messages
provide an opt-out. Administrators can disable optional modules and filter
rules. There is no universal per-user switch that exempts a member from server
moderation or all membership processing while using the same community.

For access, correction or deletion, provide your Discord user ID, relevant
server and records/feature through the contact methods above. The operator will
verify the request and remove data no longer required. Active sanction,
abuse-prevention or legal records may need to be retained; the operator will
explain an applicable exception. Deleting a Discord message or leaving a server
does not automatically erase every bot record or audit attachment.

## Changes

Changes to the hosted application's data practices will be reflected here
with an updated date.
