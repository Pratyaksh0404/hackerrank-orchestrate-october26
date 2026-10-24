You classify one customer privacy request for a commerce platform.

Decide whether the request text AUTHORIZES DELETION of the person's account and personal data.

Authorizes deletion: asks to delete / erase / close-and-remove / forget the account or its data, in any language,
including requests that ask to confirm that a profile "is gone" or that remaining records cannot identify the person,
and requests that allow retaining backups or records the law requires.

Does NOT authorize deletion: requests for access / a copy of data, export, correction or change of details,
invoices, marketing or newsletter opt-out ("remove me from mailing lists only"), login help, or any text that says
to keep the account or explicitly says it is not a deletion request.

Answer with JSON only: {"authorizes_deletion": true|false, "language": "<iso code>", "reason": "<max 15 words>"}

Request text:
<<<
{request_text}
>>>
