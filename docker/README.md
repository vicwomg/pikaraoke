
# Dockerized PiKaraoke

PiKaraoke is a "KTV"-style karaoke song search and queueing system, originally designed to work on Raspberry Pi, OSX, Windows, and Linux. This repository contains a Dockerized version of PiKaraoke, built on an alpine base, making it easy to set up and run on any system with Docker support.

The original project can be found <a href="https://github.com/vicwomg/pikaraoke">here</a>

## Features

The Dockerized version retains all the features of the original PiKaraoke, including:

- Web interface for multiple users to queue tracks
- Player/splash screen with connection QR code and "Next up" display
- Searching/browsing a local song library
- Adding new songs from YouTube
- mp3 + cdg support, including compressed .zip bundles
- Pause/Skip/Restart and volume control
- Advanced editing of downloaded file names
- Queue management
- Key Change / Pitch shifting
- Lock down features with admin mode

(Refer to the original PiKaraoke features for more details.)

---

## Support the Original Project

If you want to support the original PiKaraoke project with a monetary tip, it's much appreciated by the original developer:

[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/vicwomg)

---


## Running Dockerized PiKaraoke

### Prerequisites

- Docker and Docker Compose installed on your system

### Installation and Launch


## Using Docker Compose Command Line

1. **Clone the Repository**:
   First, clone the repository from GitHub to get the `docker-compose.yml` file and any other necessary configurations:
   ```bash
   git clone https://github.com/honestlai/pikaraoke-docker.git
   cd pikaraoke-docker
   ```

2. **Running the Container**:
   Use Docker Compose to pull the image from Docker Hub and start the container in detached mode:
   ```bash
   docker-compose up -d
   ```

## Using Portainer

1. **Access Portainer**:
   Open Portainer and navigate to the 'Stacks' section in the left sidebar.

2. **Add a New Stack**:
   Click on '+ Add stack'. Enter a name for the stack in the 'Name' field.

3. **Compose File**:
   Clone the repository from GitHub or copy the contents of the `docker-compose.yml` file into the 'Web editor'. The repository can be cloned using:
   ```bash
   git clone https://github.com/honestlai/pikaraoke-docker.git
   ```

4. **Environment Variables**:
   Below the web editor, use the 'Add an environment variable' button to add necessary environment variables. The primary variables you might need are:
   - `URL`: The URL for the pikaraoke service.
   - `PASSWORD`: The admin password for pikaraoke (optional).

5. **Deploy the Stack**:
   After setting up your compose file and environment variables, click on 'Deploy the stack'. Portainer will pull the image from Docker Hub and start the container based on your configurations.

## Building the container locally

1. **Clone this Dockerized repository:**

   ```bash
   git clone https://github.com/honestlai/pikaraoke-docker.git
   cd pikaraoke-docker
   ```

2. **Build and run the Docker container:**

   ```bash
   docker-compose up --build
   ```

   This command builds the Docker image and starts the PiKaraoke server. 

3. **Accessing PiKaraoke:**

   After the container is up and running, PiKaraoke will be accessible at `http://localhost:8888`. You can connect to this address from any device on the same network to access the PiKaraoke web interface.

### Customizing Your Setup

- Configuration options can be adjusted through environment variables in the `docker-compose.yml` file.
- Songs are stored within a docker volume, and you can manage this storage as per your requirements.

---

## Screenshots

### TV

<p float="left">
  <img width="400" alt="pikaraoke-tv1" src="https://user-images.githubusercontent.com/4107190/95813571-06645600-0ccd-11eb-8341-021a20813990.png">
<img width="400" alt="pikaraoke-tv2" src="https://user-images.githubusercontent.com/4107190/95813564-019fa200-0ccd-11eb-95e1-57a002c357a3.png">
  </p>

### Web interface

<div style="display: flex">
<img width="250" alt="pikaraoke-nowplaying" src="https://user-images.githubusercontent.com/4107190/95813193-2cd5c180-0ccc-11eb-89f4-11a69676dc6f.png">
<img width="250" alt="pikaraoke-queue" src="https://user-images.githubusercontent.com/4107190/95813195-2d6e5800-0ccc-11eb-8f00-1369350a8a1c.png">
<img width="250"  alt="pikaraoke-browse" src="https://user-images.githubusercontent.com/4107190/95813182-27787700-0ccc-11eb-82c8-fde7f0a631c1.png">
<img width="250"  alt="pikaraoke-search1" src="https://user-images.githubusercontent.com/4107190/95813197-2e06ee80-0ccc-11eb-9bf9-ddb24d988332.png">
<img width="250"  alt="pikaraoke-search2" src="https://user-images.githubusercontent.com/4107190/95813190-2ba49480-0ccc-11eb-84e3-f902cbd489a2.png">
</div>
