# FastAPI study notes

FastAPI uses Python type annotations to describe request parameters and response models.
Pydantic performs validation for structured request data. Invalid input produces a 422 response.
Dependency injection lets routes share database connections and authentication checks.
The lifespan context initializes resources when the application starts.
Use synchronous route functions for blocking SQLite operations in this project.
