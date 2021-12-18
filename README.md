# Trends.Earth API

This project belongs to the Trends.Earth project.

This repo implements the API used by the Trends.Earth plugin and web
interfaces. It implements the Scripts, Users and Executions management.

Check out the other parts of the Trends.Earth project:

-   The Command Line Interface. It allows to create and test custom
    scripts locally. It also can be used to publish the scripts to the
    Trends.Earth Environment
    [(Trends.Earth CLI)](https://github.com/conservationinternational/trends.earth-CLI)
-   The [Trends.Earth Core
    Environment](https://github.com/conservationinternational/trends.earth-Environment)
    used for executing scripts in Trends.Earth
-   A web app to explore and manage the API entities [(Trends.Earth
    UI)](https://github.com/conservationinternational/trends.earth-UI)

## Getting started

### Requirements

You need to install Docker in your machine if you haven't already
[(Docker)](https://www.docker.com/)

### Technology

-   Docker is used in development and production environment
-   The API is coded in Python 3.6
-   It uses Flask to expose the API Endpoints and handle the HTTP
    requests
-   It also uses SQLAlchemy as ORM (PostgreSQL)
-   Celery is used to manage the background tasks (Redis)
-   In production mode, the API will be deployed using Gunicorn

## Development

Follow the next steps to set up the development environment in your
machine or on a cloud server

### For local development (without docker swarm)

1.  Clone the repo and navigate to the folder

``` ssh
git clone https://github.com/conservationinternational/trends.earth-api
cd GEF-API
```

2.  Build the docker image:

``` ssh
docker build -t trendsearth-api .
```

2.  Start the services:

``` ssh
docker-compose -f docker-compose-develop.yml up
```

4.  To stop the services:

``` ssh
docker-compose down
```

### On docker swarm (used on staging)

1.  Clone the repo and navigate to the folder

``` ssh
git clone https://github.com/conservationinternational/trends.earth-api
cd GEF-API
```

2.  Build the docker image:

``` ssh
docker build -t trendsearth-api .
```

2.  Start a stack running on docker swarm

    -   To run local development version on docker swarm:

        ``` ssh
        docker stack deploy -c docker-compose-develop.yml api
        ```

    -   To run on staging:

        ``` ssh
        docker stack deploy -c docker-compose-staging.yml api
        ```

3.  Once the services are up:

    -   To check the services are running:

        ``` ssh
        docker stack ps api
        ```

    -   To check the logs for the services:

        ``` ssh
        docker logs -f api_manager
        ```

        ``` ssh
        docker logs -f api_worker
        ```

        ``` ssh
        docker logs -f redis
        ```

4.  To remove the stack:

    To check on the services:

    ``` ssh
    docker stack rm api
    ```

If this is the first time you run it, it may take a few minutes.

## Production deploy

### Setup

1.  Start a stack on docker swarm (using `docker swarm init` if needed)

2.  asdf

### Code structure

The API has been packed in a Python module (gefapi). It creates and
exposes a WSGI application. The core functionality has been divided in
three different layers or submodules (Routes, Services and Models).

There are also some generic submodules that manage the request
validations, HTTP errors and the background tasks manager.

### Entities Overview

#### Script

    id: <UUID>
    name: <String>
    slug: <String>, unique
    created_at: <Date>
    user_id: <UUID>
    status: <String>
    logs: <- [ScriptLog]
    executions: <- [Execution]
    public: <Boolean>
    cpu_reservation: <Integer>
    cpu_limit: <Integer>
    memory_reservation: <Integer>
    memory_limit: <Integer>

#### Execution

    id: <UUID>
    start_date: <Date>
    end_date: <Date>
    status: <String>
    progress: <Integer>
    params: <Dict>
    results: <Dict>
    logs: <- [ExecutionLog]
    script_id: <- Script
    user_id: <UUID>
    is_plugin_execution: <Boolean>
    deleted: <Boolean>

#### User

    id: <UUID>
    created_at: <Date>
    email: <String>, unique
    password: <String>, encrypted
    role: <String>
    is_plugin_user: <Boolean>
    is_in_mailing_list: <Boolean>
    scripts: <- [Script]
    executions: <- [Execution]
    deleted: <Boolean>

## API Endpoints

### Script

    GET: /api/v1/script
    GET: /api/v1/script/<script>
    POST: /api/v1/script
    PATCH: /api/v1/script/<script>
    DELETE: /api/v1/script/<script>
    GET: /api/v1/script/<script>/log

### Execution

    GET: /api/v1/script/<script>/run
    GET: /api/v1/execution
    GET: /api/v1/execution/<execution>
    PATCH: /api/v1/execution/<execution>
    GET: /api/v1/execution/<execution>/log
    POST: /api/v1/execution/<execution>/log

### User

    GET: /api/v1/user
    GET: /api/v1/user/<user>
    GET: /api/v1/user/me
    POST: /api/v1/user
    PATCH: /api/v1/user/<user>
    PATCH: /api/v1/user/me
    DELETE: /api/v1/user/<user>
    DELETE: /api/v1/user/me
    POST: /api/v1/user/<user>/recover-password

### Auth

    POST: /auth

### Email

    POST: /email
