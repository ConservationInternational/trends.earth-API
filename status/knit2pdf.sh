Rscript -e 'library(rmarkdown); rmarkdown::render("usage_report.Rmd", "pdf_document")'
mv usage_report.pdf /public/index.pdf
chmod 644 /public/index.pdf
