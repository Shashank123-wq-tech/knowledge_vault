# Durable background processing

The API saves a document and an indexing job in the same transaction.
A separate Python worker claims queued jobs with a lease token.
An expired lease can be reclaimed after a worker crash. Old workers cannot publish after the lease is replaced.
Text is divided into overlapping chunks. Index writes and successful job status commit atomically.
After three unsuccessful attempts the job fails. A user may explicitly retry their failed job.
