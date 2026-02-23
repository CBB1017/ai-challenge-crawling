pipeline {
    agent any

    environment {
        NETWORK_NAME = 'attendance'
        IMAGE_NAME   = 'crawling'
        IMAGE_TAG    = "${BUILD_NUMBER}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Ensure Docker Network') {
            steps {
                sh '''
                if ! docker network inspect ${NETWORK_NAME} >/dev/null 2>&1; then
                  docker network create ${NETWORK_NAME}
                fi
                '''
            }
        }

        stage('Build Crawling Image') {
            steps {
                sh '''
                docker build \
                  --build-arg BUILD_NUMBER=${BUILD_NUMBER} \
                  -t ${IMAGE_NAME}:${IMAGE_TAG} \
                  -t ${IMAGE_NAME}:latest \
                  .
                '''
            }
        }

        stage('Deploy with Docker Compose') {
            steps {
                sh 'docker compose up -d --build'
            }
        }
    }
}
