# Bundesagentur through its API, Indeed through alert mail, no portal scraping

Postings from the Bundesagentur für Arbeit come from its Jobsuche REST API, which filters by radius on the server and gives a stable `refnr`. Indeed has no usable API and blocks automated fetching, so its Postings come from alert mails read over IMAP, and those Jobs are scored on the snippet unless the same Job is found with a full description at another Source. Bundesagentur alert mails are deliberately not used, and mails from other senders are ignored.
