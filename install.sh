#!/bin/bash

(cd docker && docker build --no-cache -t yumayo-ai -f Dockerfile .)
