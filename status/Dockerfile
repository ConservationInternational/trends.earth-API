FROM rocker/r2u:24.04
MAINTAINER Alex Zvoleff azvoleff@conservation.org

RUN apt-get update && \
    apt-get install -yq git pandoc \
        r-cran-tidyverse r-cran-rpostgres r-cran-jsonlite \
        r-cran-geojsonsf r-cran-ggrepel r-cran-rcolorbrewer \
        r-cran-rnaturalearth r-cran-rnaturalearthdata r-cran-cowplot r-cran-lwgeom \
        r-cran-knitr r-cran-foreach && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*  && \
    mkdir -p /work

RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen
ENV LANG en_US.UTF-8 ENV LANGUAGE en_US:en ENV LC_ALL en_US.UTF-8

RUN chown $USER:$USER /work

WORKDIR /work

ADD entrypoint.sh /work/entrypoint.sh
ADD usage_report.Rmd /work/usage_report.Rmd
ADD knit2html.sh /work/knit2html.sh

ENTRYPOINT ["./entrypoint.sh"]
