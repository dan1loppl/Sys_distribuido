"""Pacote p2p — base de comunicação e identidade (Sprint 1).

Cada módulo cuida de UM conceito, para facilitar o estudo:

    config.py       configuração persistente (UUID, label, host, port)
    ndjson.py       enquadramento: transforma o fluxo de bytes do TCP em linhas
    envelope.py     formato padrão das mensagens + validação
    dispatcher.py   escolhe a função que trata cada "type"
    correlation.py  liga cada resposta à sua solicitação (request_id)
    connection.py   socket TCP + buffer + laço de leitura + envio seguro
    registry.py     cadastro de workers no master (ONLINE/OFFLINE)
    master.py       o processo master
    worker.py       o processo worker
    logs.py         logs padronizados (grupo, origem, destino, type, request_id)
"""
