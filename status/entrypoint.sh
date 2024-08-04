#!/bin/bash
set -e


case "$1" in
    knit2html)
        echo "Knitting to HTML..."
        exec ./knit2html.sh
		;;
    knit2pdf)
        echo "Knitting to PDF..."
        exec ./knit2pdf.sh
		;;
    *)
        exec "$@"
esac
