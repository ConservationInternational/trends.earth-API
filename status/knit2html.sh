Rscript -e 'library(rmarkdown); rmarkdown::render("usage_report.Rmd", "html_document")'
mv usage_report.html /public/index.html
chmod 644 /public/index.html
