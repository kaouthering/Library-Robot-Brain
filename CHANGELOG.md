# Changelog


## Unreleased


* Renamed the project to **Library Robot Brain**.
* README now states clearly that this project is the reasoning brain, and that the robot body is simulated.


## 1.0.0


* Web interface: desk (borrow / return / reserve with proofs), books, loans, members, catalogue, "ask the logic".
* QR scanning from a photo uploaded in the browser; printable QR code for every book.
* New `service.py`: all library operations return the logical proof of the decision.
  A member can no longer take a second copy of the same book by accident.
* Code organised as an installable package (`pip install .`, command `library-robot`).
* Tests: reasoning engine, service, web API (57 tests) and GitHub Actions workflow.
* Earlier: logic engine, real QR camera reading, SQLite persistence, Tkinter simulation.
