.PHONY: build run clean

IMAGE_TAG ?= nautilus-python
EIF_FILE ?= nautilus-python.eif

build:
	docker build --platform linux/amd64 -t $(IMAGE_TAG) -f Containerfile .

run:
	python3 app.py

clean:
	rm -f $(EIF_FILE) pcrs.json
