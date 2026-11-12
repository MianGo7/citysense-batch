# citysense-batch

A batch oriented data system for municipal environmental sensor data. Sensor
readings are loaded in daily batches into a MongoDB replica set, implausible
readings are flagged, hourly and daily summaries and the latest value per
sensor are derived for a planning dashboard and a citizen warning
application, and alerts are raised without repeating themselves. The whole
system starts from a fresh clone with Docker as its only requirement.

This repository is the practical part of a portfolio for the IU course
DLBDSEDE02, Project: Data Engineering.

- Course: DLBDSEDE02, Project: Data Engineering
- Task: Task 1, choose a suitable database and store the data in batches
- Author: Mian Gohar Ehsan
- Matriculation number: IU14147184
- Tutor: Prof. Dr.-Ing. Paul Libbrecht

The system is under construction. The technical concept is in
`docs/concept.md`, the decisions in `docs/decision-log.md`.
