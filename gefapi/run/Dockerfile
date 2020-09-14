FROM conservationinternational/trends.earth-environment:0.1.2

COPY src /project/gefcore/script
COPY requirements.txt /project/requirements.txt

RUN pip install -r /project/requirements.txt

USER $USER
