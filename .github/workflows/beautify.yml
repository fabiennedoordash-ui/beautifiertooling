name: Beautify Catalog Images

on:
  workflow_dispatch:
    inputs:
      urls:
        description: Newline-separated image URLs to beautify
        required: true
        type: string
      prompt:
        description: Custom enhancement prompt (optional)
        required: false
        default: ""
        type: string
      start_from:
        description: Skip first N URLs (for resuming)
        required: false
        default: "0"
        type: string

jobs:
  beautify:
    runs-on: ubuntu-latest
    timeout-minutes: 120

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Write URLs to file
        run: |
          cat << 'URLS_EOF' > urls.txt
          ${{ github.event.inputs.urls }}
          URLS_EOF
          echo "URL count: $(wc -l < urls.txt)"

      - name: Run beautifier
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          IMGBB_API_KEY: ${{ secrets.IMGBB_API_KEY }}
        run: |
          PROMPT_ARG=""
          if [ -n "${{ github.event.inputs.prompt }}" ]; then
            PROMPT_ARG="--prompt ${{ github.event.inputs.prompt }}"
          fi
          python beautify.py \
            --input urls.txt \
            --output output/ \
            --start-from ${{ github.event.inputs.start_from }} \
            $PROMPT_ARG

      - name: Upload enhanced images
        uses: actions/upload-artifact@v4
        with:
          name: beautified-images-${{ github.run_number }}
          path: output/
          retention-days: 30

      - name: Post summary
        if: always()
        run: python summary.py >> $GITHUB_STEP_SUMMARY
