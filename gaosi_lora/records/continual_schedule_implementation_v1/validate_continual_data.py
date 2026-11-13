"""Reject overlapping/future probe bindings and incomplete continual queries."""
import argparse
import json
from pathlib import Path
import tempfile

from continual_queue import validate_stream_data


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default='checks/continual_data_isolation_cpu.json')
    args=parser.parse_args()
    tests={}
    with tempfile.TemporaryDirectory(prefix='gaosi_dev_stream_ids_') as directory:
        files=[]
        for index,count in enumerate([512]+[32]*12):
            file=Path(directory)/f'dev_{index}.jsonl'
            data=[{'fact_id':f'dev{index}:{key}','role':role,'sample_id':f'dev{index}:{key}:{role}:0'}
                  for key in range(count) for role in ['support','query']]
            file.write_text(''.join(json.dumps(row)+'\n' for row in data));files.append(str(file))
        counts=validate_stream_data(files[0],files[1:9],files[9:])
        assert len(counts)==13 and sum(counts.values())==896
        tests['full_parent_512_stream_8x32_and_probes_4x32_are_pairwise_disjoint']='PASS'
        target=Path(files[-1]);original=target.read_text()
        data=[json.loads(line) for line in original.splitlines()]
        for row in data[:2]:row['fact_id']='dev0:0'
        target.write_text(''.join(json.dumps(row)+'\n' for row in data))
        try:validate_stream_data(files[0],files[1:9],files[9:])
        except ValueError:pass
        else:raise AssertionError('Shared probe/parent binding accepted')
        tests['probe_binding_that_was_already_learned_in_parent_is_rejected']='PASS'
        target.write_text(original)
        data=[json.loads(line) for line in original.splitlines()]
        target.write_text(''.join(json.dumps(row)+'\n' for row in data if row!=data[-1]))
        try:validate_stream_data(files[0],files[1:9],files[9:])
        except ValueError:pass
        else:raise AssertionError('Incomplete query set accepted')
        tests['missing_query_binding_is_rejected']='PASS'
        target.write_text(''.join(json.dumps(row)+'\n' for row in data[:-2]))
        try:validate_stream_data(files[0],files[1:9],files[9:])
        except ValueError:pass
        else:raise AssertionError('Thirty-one-fact new task accepted')
        tests['a_world_with_less_than_32_bindings_is_rejected']='PASS'
        target.write_text(original)
        try:validate_stream_data(files[0],files[1:9],files[8:12])
        except ValueError:pass
        else:raise AssertionError('Probe path from learning stream accepted')
        tests['learning_world_reused_as_capacity_probe_is_rejected']='PASS'
    result={'status':'PASS','scope':'Synthetic ID fixtures only; no formal rows or GPU use','tests':tests}
    Path(args.out).write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':
    main()
