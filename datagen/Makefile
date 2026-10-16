# Cymbal Foods dataset. `make` goes from nothing to out/ (Parquet, GA4, SQL, BigQuery schemas, validation, slices).
PY ?= python3
OUT ?= out
STAGE ?= gs://class-demo/alloydb-labs/cymbalfoods-alloydb-workshop/dataset/v1

.PHONY: all setup check generate slices stage pgcheck clean

all: generate

setup:                 ## install the libraries
	$(PY) -m pip install -r requirements.txt

check:                 ## validate content/ and print projected product and SKU counts
	$(PY) -m cfgen.content

generate:              ## generate, validate and cut every slice into $(OUT)/  (~17 min on 2 vCPU, most of it GA4)
	$(PY) generate_dataset.py --out $(OUT)

slices:                ## re-cut slices.yaml from the existing Parquet, no regeneration
	$(PY) generate_dataset.py --out $(OUT) --cut-only

stage:                 ## copy $(OUT)/ to the course bucket (pipeline/10 does this with a guard)
	gcloud storage rsync --recursive $(OUT) $(STAGE)

pgcheck:               ## load a slice into a local PostgreSQL the way the setup VM will: make pgcheck SLICE=aldb005_ops
	$(PY) tools/pg_load_check.py $(OUT)/slices/$(SLICE) --db cf_$(SLICE)

clean:
	rm -rf $(OUT)
