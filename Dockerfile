FROM python:3.12-slim AS app

ARG LIMSPORT_VER="0.1.0"

LABEL base.image="python:3.12-slim"
LABEL dockerfile.version="1"
LABEL software="LIMSport"
LABEL software.version="${LIMSPORT_VER}"
LABEL description="Renames, selects, and QCs the columns of a TSV of samples to produce a LIMS-importable TSV"
LABEL website="https://github.com/theiagen/limsport"
LABEL license="https://github.com/theiagen/limsport/blob/main/LICENSE"
LABEL maintainer="Sage Wright"
LABEL maintainer.email="sage.wright@theiagen.com"

RUN apt-get update && apt-get install -y --no-install-recommends \
    procps && \
    apt-get autoclean && rm -rf /var/lib/apt/lists/*

# gcloud is used by limsport's file_parsing to download gs:// paths
COPY --from=gcr.io/google.com/cloudsdktool/google-cloud-cli:590.0.0-slim /usr/lib/google-cloud-sdk /usr/lib/google-cloud-sdk
ENV PATH="/usr/lib/google-cloud-sdk/bin:${PATH}"

COPY pyproject.toml LICENSE /limsport/
COPY limsport /limsport/limsport
RUN pip install --no-cache-dir /limsport && \
    rm -rf /limsport

ENV LC_ALL=C

CMD [ "limsport", "--help" ]

WORKDIR /data

FROM app AS test

WORKDIR /test

RUN limsport --help && \
    limsport --version && \
    gcloud --version

# run the repository's examples and check they reproduce the expected outputs
COPY examples /test/examples
RUN cd examples && \
    cp -r outputs expected && \
    bash run_examples.sh && \
    for f in expected/*.tsv; do diff "${f}" "outputs/$(basename ${f})" || exit 1; done
