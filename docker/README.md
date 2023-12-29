
# Dockerized PiKaraoke

This repository contains a Dockerized version of PiKaraoke, a "KTV"-style karaoke song search and queueing system, originally designed to work on Raspberry Pi, OSX, Windows, and Linux. This container is built on alpine, making it easy to set up and run on any system with docker support.

## Features

- The Dockerized version retains all features of the original PiKaraoke.
- Easy to set up and run on any system with Docker support.
- Automatically exposes on port 5555.

## Prerequisites

- Docker and Docker Compose installed on your system.
- A reverse proxy like nginx is very helpful but not required.
- The URL variable is optional, but will set the system to operate and function from the docker hosts hosthame, and only be accessible locally. The player/splashpage will be viewable at http://docker-hostname:5555/splash
- If using a reverse proxy http:// or https:// is required at the beginning of the URL variable
- The PASSWORD variable is optional.

---

For more details on the project and additional features, please visit the [main repository page](https://github.com/vicwomg/pikaraoke).

---

## Installation and Launch

### Using Docker Compose Command Line

1. **Create a Docker Compose File**:
   Create a `docker-compose.yml` file with the following content:
   ```yaml
   version: '3'

   services:
     pikaraoke:
       image: honestlai/pikaraoke-docker:latest
       container_name: PiKaraoke
       volumes:
         - /etc/hostname:/etc/host_hostname:ro       
         - pikaraoke-songs:/pikaraoke-songs
       environment:
         URL: #optional https://karaoke.yourdomain.com
         PASSWORD: #optionalpassword
       restart: unless-stopped
       ports:
         - "5555:5555"

   volumes:
     pikaraoke-songs:
       # Define your volume specifics here, if any.
   ```

2. **Running the Container**:
   Use Docker Compose to pull the image and start the container:
   ```bash
   docker-compose up -d
   ```

### Using Portainer

1. **Access Portainer**: Navigate to the 'Stacks' section.
2. **Add a New Stack**: Click on '+ Add stack'.
3. **Compose File**: Clone the repository or copy the `docker-compose.yml` content above.
4. **Environment Variables**: Add necessary variables like `URL` and `PASSWORD`.
5. **Deploy the Stack**: Click on 'Deploy the stack'.

### Building the Container Locally

1. **Clone this Dockerized repository**:
   ```bash
   git clone https://github.com/vicwomg/pikaraoke.git
   cd pikaraoke/docker
   ```

2. **Build and Run the Docker Container**:
   ```bash
   docker-compose up --build
   ```


