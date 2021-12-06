FROM conservationinternational/trends.earth-environment:0.1.4

COPY src /project/gefcore/script
COPY requirements.txt /project/requirements.txt

RUN pip install --no-cache-dir -r /project/requirements.txt

USER $USER
