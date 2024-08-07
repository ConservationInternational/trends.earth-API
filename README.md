# Trends.Earth API

This project belongs to the Trends.Earth project.

This repo implements the API used by the Trends.Earth plugin and web
interfaces. It implements the Scripts, Users and Executions management.

Check out the other parts of the Trends.Earth project:

- The Command Line Interface. It allows to create and test custom
  scripts locally. It also can be used to publish the scripts to the
  Trends.Earth Environment
  [(Trends.Earth CLI)](https://github.com/conservationinternational/trends.earth-CLI)
- The [Trends.Earth Core
  Environment](https://github.com/conservationinternational/trends.earth-Environment)
  used for executing scripts in Trends.Earth
- A web app to explore and manage the API entities [(Trends.Earth
  UI)](https://github.com/conservationinternational/trends.earth-UI)

## Getting started

### Requirements

You need to install Docker in your machine if you haven't already
[(Docker)](https://www.docker.com/)

### Technology

- Docker is used in development and production environment
- The API is coded in Python 3.6
- It uses Flask to expose the API Endpoints and handle the HTTP
  requests
- It also uses SQLAlchemy as ORM (PostgreSQL)
- Celery is used to manage the background tasks (Redis)
- In production mode, the API will be deployed using Gunicorn

## Development

Follow the next steps to set up the development environment in your
machine or on a cloud server

### For local development (without docker swarm)

1.  Clone the repo and navigate to the folder

```ssh
git clone https://github.com/conservationinternational/trends.earth-api
cd trends.earth-api
```

2.  Build the docker image:

```ssh
docker compose -f docker-compose.staging.yml build
```

2.  Start the services:

```ssh
docker compose -f docker-compose.staging.yml up
```

4.  To stop the services:

```ssh
docker compose -f docker-compose.staging.yml down
```

### On docker swarm (used on staging)

1.  Clone the repo and navigate to the folder

```ssh
git clone https://github.com/conservationinternational/trends.earth-api
cd trends.earth-api
```

2.  Build the docker image:

```ssh
docker build -t 172.40.1.52:5000/trendsearth-api-staging .
docker push 172.40.1.52:5000/trendsearth-api-staging
```

2.  Start a stack running on docker swarm

    ```ssh
    docker stack deploy -c docker-compose.staging.yml api-staging
    ```

3.  Once the services are up:

    - To check the services are running:

      ```ssh
      docker stack ps api-staging
      ```

    - To check the logs for the services:

      ```ssh
      docker logs -f api_manager-staging
      ```

      ```ssh
      docker logs -f api_worker-staging
      ```

      ```ssh
      docker logs -f redis-staging
      ```

4.  To remove the stack:

    ```ssh
    docker stack rm api-staging
    ```

### Running a container for maintenance (db migration, etc.)

1.  Startup a maintenance container

```ssh
docker compose -f docker-compose.admin.yml up
```

To run in the background (so you can connect to container via another process), add
`-d`:

```ssh
docker compose -f docker-compose.admin.yml build
docker compose -f docker-compose.admin.yml up -d
```

2.  Run whatever needs to be done (db migration, etc.). For example

```ssh
docker exec -it trendsearth-api-admin-1 /bin/bash
flask db migrate
flask db upgrade
exit
```

3.  Shut it down

```ssh
docker compose -f docker-compose.admin.yml down
```

### Deployment

#### Deploy nginx server

1.  Add a `nginx-certbot.env` file specifying the `CERTBOT_EMAIL` to use

2.  Startup the nginx container in the background

```ssh
docker compose -f docker-compose-nginx.yml up -d
```

#### Deploy API stack

1.  Build image and push to registry

    ```ssh
    docker build -t 172.40.1.52:5000/trendsearth-api .
    docker push 172.40.1.52:5000/trendsearth-api
    ```

2.  Start the stack

    ```ssh
    docker stack deploy -c docker-compose.prod.yml api
    ```

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
