image := ynput/ayon-dependencies-tool:0.0.1

build:
	cp -r ./../dependencies .
	cp -r ./../pyproject.toml .
	docker build -t $(image) .
	rm -rf dependencies
	rm pyproject.toml

clean:
	if docker images | grep $(image); then \
		docker rmi $(image); \
	fi

clean-build: clean build

dev:
	docker run --rm -u ayonuser -ti \
		-v $(CURDIR):/service:Z \
		--env-file ./../.env \
		--attach=stdin \
		--attach=stdout \
		--attach=stderr \
		--network=host \
		$(image) python /service/listener.py

shell:
	docker run --rm -u ayonuser -ti -v $(CURDIR):/service:Z $(image) /bin/sh