import argparse
import torch
import pickle
import os
import numpy as np
import time
import random
import logging
from graph_data import stock_stock_data, stock_concept_data
from models.ts_model import Graphs
from dataset import DatasetH
from interpreter.attentionx import AttentionX
from interpreter.xpath import xPath
from interpreter.subgraphx import SubgraphXExplainer


def load_graph(market, relation_source, data_mix):
    indexs = data_mix.index.levels[1].tolist()
    indexs = list(set(indexs))
    stocks_sorted_list = sorted(indexs)
    # print(stocks_sorted_list)
    # print("number of stocks: ", len(stocks_sorted_list))
    stocks_index_dict = {}
    for i, stock in enumerate(stocks_sorted_list):
        stocks_index_dict[stock] = i
    n = len(stocks_index_dict.keys())
    if relation_source == 'stock-stock':
        rel_encoding = stock_stock_data.get_all_matrix(
            market, stocks_index_dict,
            # data_path="D:/Code/myqlib/.qlib/qlib_data/graph_data/"
            data_path=os.path.join(args.data_root, 'graph_data')  # This is the graph_data path on the server
        )
        return rel_encoding, stocks_sorted_list
    elif relation_source == 'industry':
        industry_dict = stock_concept_data.read_graph_dict(
            market,
            relation_name="SW_belongs_to",
            # data_path="D:/Code/myqlib/.qlib/qlib_data/graph_data/",
            data_path=os.path.join(args.data_root, 'graph_data')
        )
        return stock_concept_data.get_full_connection_matrix(
            industry_dict, stocks_index_dict
        ), stocks_sorted_list
    elif relation_source == 'full':
        return np.ones(shape=(n, n)), stocks_sorted_list
    else:
        raise ValueError("unknown graph name `%s`" % relation_source)


def init_logger(log_dir, args):
    os.makedirs(log_dir, exist_ok=True)
    # current_time = time.strftime("%Y-%m-%d-%H-%M", time.localtime())
    log_file = log_dir + f'/{args.graph_model}.log'
    file_handler = logging.FileHandler(log_file)
    console_handler = logging.StreamHandler()
    fmt = '%(asctime)s - %(funcName)s - %(lineno)s - %(levelname)s - %(message)s'
    formatter = logging.Formatter(fmt)
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger = logging.getLogger('updateSecurity')
    logger.setLevel('INFO')
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_model(args, dataset, device):
    with open(
            f'{args.data_root}/dataframe_mix_csi300_rankTrue_alpha360_horizon1.pkl',
            'rb') as f:
        data_mix= pickle.load(f)
    rel_encoding, stock_name_list = load_graph(args.market, args.relation_type, data_mix['data'])
    model = Graphs(graph_model=args.graph_model,  # 'GAT' or 'simpleHGN', 'RSR'
                   d_feat=6, hidden_size=64, num_layers=2, loss="mse", dropout=0.7, n_epochs=100,
                   metric="loss", base_model="LSTM", use_residual=True, GPU=args.gpu, lr=1e-3,
                   early_stop=10, rel_encoding=rel_encoding, stock_name_list=stock_name_list,
                   num_graph_layer=2, logger=init_logger('../log', args))
    model.to(device)
    model_path = os.path.join(args.ckpt_root, f"{args.market}-{args.graph_model}-{args.graph_type}.pt")
    if os.path.exists(model_path):
        model.load_checkpoint(model_path)
        df_test = dataset.prepare("test", col_set=["feature", "label"], data_key="infer")
        x_test, y_test = df_test["feature"], df_test["label"]
        # test_loss, test_score, test_IC = model.test_epoch(x_test, y_test)
        # print(f"test loss: {test_loss}, test score: {test_score}, test IC: {test_IC}")
        print("evaluating...")
        test_loss, test_IC, test_RIC = model.test_epoch(x_test, y_test)
        print("test loss %.4f, test IC %.4f, test RIC %.4f" % (test_loss, test_IC, test_RIC))
    else:
        model.fit(dataset, save_path=model_path)

    return model


def get_dataset():
    train_start_date = '2008-01-01'
    train_end_date = '2014-12-31'
    valid_start_date = '2015-01-01'
    valid_end_date = '2016-12-31'
    test_start_date = '2017-01-01'
    test_end_date = '2022-12-31'

    with open(f'{args.data_root}/dataframe_mix_csi300_rankTrue_alpha360_horizon1.pkl', 'rb') as f:
        df_mix = pickle.load(f)
    dataset = DatasetH(df_mix, train_start_date, train_end_date, valid_start_date,
                       valid_end_date, test_start_date, test_end_date)
    # df_test = dataset.prepare("test", col_set=["feature", "label"])
    return dataset


def eval_explanation(explainer, explainer_name, sparsity, step_size):
    res_path = os.path.join(args.result_root,
                            f"{args.market}-{args.graph_model}-{args.graph_type}-{explainer_name}-explanation")
    if os.path.exists(res_path):
        with open(res_path, 'rb') as f:
            explanation = pickle.load(f)
    else:
        explanation, scores = model.get_explanation(dataset, explainer, debug=True, step_size=step_size)
        exp = {}
        exp['explainer'] = explainer_name
        exp['explanation'] = explanation
        exp['scores'] = scores
        print('Saving explanations...')
        with open(res_path, 'wb') as f:
            pickle.dump(exp, f)

    res_explainer = {}
    for i, k in enumerate(sparsity[args.graph_model][explainer_name]):
        _, fidelity = model.get_explanation(dataset, explainer, step_size=step_size, top_k=k,
                                            cached_explanations=explanation['explanation'])
        res_explainer[i+3] = sum(fidelity) / len(fidelity)
    return res_explainer


def parse_args():
    parser = argparse.ArgumentParser(description="Explanation evaluation.")
    parser.add_argument("--data_root", type=str, default="/home/jiale/.qlib/qlib_data/", help="graph_data root path")
    parser.add_argument("--ckpt_root", type=str, default="/home/jiale/interpreters/tmp_ckpt/", help="ckpt root path")
    parser.add_argument("--result_root", type=str, default="/home/jiale/interpreters/results/",
                        help="explanation resluts root path")
    parser.add_argument("--market", type=str, default="A_share",
                        choices=["A_share"], help="market name")
    parser.add_argument("--relation_type", type=str, default="stock-stock",
                        choices=["stock-stock", "industry", "full"], help="relation type of graph")
    parser.add_argument("--graph_model", type=str, default="RSR",
                        choices=["RSR", "GAT", "GCN", "simpleHGN",
                                  "GSAT_RSR", "GSAT_GAT", "GSAT_GCN",
                                  "SNex_RSR", "SNex_GAT", "SNex_GCN"], help="graph moddel name")
    parser.add_argument("--graph_type", type=str, default="heterograph",
                        choices=["heterograph", "homograph"], help="graph type")
    parser.add_argument("--gpu", type=int, default=0, help="gpu number")
    args = parser.parse_args()
    return args


def run_all_test():
    # hyper-parameters
    sparsity = {
        'RSR': {'effect': [5, 9], 'xpath': [3, 5], 'subgraphx': [4, 8]},
        'simpleHGN': {'effect': [3, 5], 'xpath': [3, 5], 'subgraphx': [4, 8]},
        'GAT': {'effect': [3, 5], 'xpath': [3, 5], 'subgraphx': [3, 6]},
    }

    graph_type = args.graph_type  # for GAT, use 'homograph'
    attn_explainer = AttentionX(graph_model=graph_type, num_layers=1, device=device)
    xpath_explainer = xPath(graph_model=graph_type, num_layers=1, device=device)
    subagraphx_explainer = SubgraphXExplainer(graph_model=graph_type, num_layers=1, device=device)

    res = {}
    res['effect'] = eval_explanation(attn_explainer, 'effect', sparsity, 100)
    res['xpath'] = eval_explanation(xpath_explainer, 'xpath', sparsity, 100)
    res['subgraphx'] = eval_explanation(subagraphx_explainer, 'subgraphx', sparsity, 200)

    print('以下是测试结果：')
    print('>>> 当解释大小限定为3时，各解释方法的平均fidelity分数分别为 <<<')
    print(f'effect: \t{round(res["effect"][3], 6)}')
    print(f'xpath: \t\t{round(res["xpath"][3], 6)}')
    print(f'subgraphx: \t{round(res["subgraphx"][3], 6)}')
    print('>>> 当解释大小限定为4时，各解释方法的平均fidelity分数分别为 <<<')
    print(f'effect: \t{round(res["effect"][4], 6)}')
    print(f'xpath: \t\t{round(res["xpath"][4], 6)}')
    print(f'subgraphx: \t{round(res["subgraphx"][4], 6)}')


def run_one_test():
    '''
    get explanations for stocks in a given time period
    '''
    stocks = ['SH600000', ]
    # stocks = None
    start_time = '2021-01-03'
    end_time = '2021-01-08'

    xpath_explainer = xPath(graph_model=args.graph_type, num_layers=1, device=device)
    attn_explainer = AttentionX(graph_model=args.graph_type, num_layers=1, device=device)
    # subagraphx_explainer = SubgraphXExplainer(graph_model=args.graph_type, num_layers=1, device=device)
    t1 = time.time()
    explanation = model.get_explanation(dataset, stocks=stocks, start_time=start_time, end_time=end_time, explainer=xpath_explainer, top_k=3)
    print(f'xpath explanation time: {time.time() - t1}')
    return explanation


def run_self_explain():
    pass


def setup_seed(seed):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)
    random.seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


if __name__ == '__main__':
    setup_seed(2023)
    args = parse_args()
    device = torch.device("cuda:%d" % (args.gpu) if torch.cuda.is_available() and args.gpu >= 0 else "cpu")

    dataset = get_dataset()
    model = get_model(args, dataset, device)

    # run_one_test()
    # run_all_test()

