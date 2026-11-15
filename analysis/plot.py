#!/usr/bin/env python3
"""Regenerate every data figure from runs.csv, plus a machine-readable plot table."""
import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SELECTED = [('cassandra','read-heavy',64),('cassandra','write-heavy',128),
            ('mongo','read-heavy',64),('mongo','write-heavy',1)]
COLORS = ['#176B91', '#D26B29']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', type=Path, default=ROOT/'data/runs.csv')
    p.add_argument('--out', type=Path, default=ROOT/'build/figures')
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    with args.runs.open(newline='') as f:
        for r in csv.DictReader(f):
            groups[r['db'],r['workload'],r['server_size'],int(r['threads'])].append(r)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':7,
        'axes.labelsize':7,'axes.titlesize':8,'xtick.labelsize':6.5,'ytick.labelsize':6.5,
        'legend.fontsize':6.5,'axes.spines.top':False,'axes.spines.right':False,
        'axes.linewidth':0.6,'lines.linewidth':1.1,'savefig.dpi':300,
        'pdf.fonttype':42,'ps.fonttype':42})
    plotrows=[]
    def stats(db,w,size,t,metric,div=1):
        obs=[float(r[metric])/div for r in groups[db,w,size,t]]
        if len(obs)!=3: raise ValueError(f'Expected three observations: {db,w,size,t}')
        return mean(obs),stdev(obs),len(obs)
    def record(stem,db,w,size,t,metric,div):
        m,s,n=stats(db,w,size,t,metric,div)
        plotrows.append(dict(figure=stem,db=db,workload=w,server_size=size,threads=t,
                             metric=metric,divisor=div,mean=m,sample_sd=s,n=n))
        return m,s
    def save(fig,stem):
        fig.savefig(args.out/(stem+'.pdf'),metadata={'CreationDate':None,'ModDate':None})
        fig.savefig(args.out/(stem+'.png'),metadata={'Software':'SENG533 reproducibility'})
        plt.close(fig)
    for w,xname,rname in [('read-heavy','image9','image8'),('mixed','image5','image4'),
                          ('write-heavy','image10','image6')]:
        for stem,metric,div,label in [(xname,'throughput_ops_s',1000,'Throughput (kops/s)'),
                                     (rname,'mean_response_us',1000,'Mean response time (ms)')]:
            fig,ax=plt.subplots(figsize=(2.30,1.80),layout='constrained')
            for db,color,marker,legend in [('cassandra',COLORS[0],'o','Cassandra'),('mongo',COLORS[1],'s','MongoDB')]:
                threads=[1,4,8,16,32,64,128]
                points=[record(stem,db,w,'base',t,metric,div) for t in threads]
                if metric=='throughput_ops_s':
                    ax.errorbar(threads,[v[0] for v in points],yerr=[v[1] for v in points],
                                color=color,marker=marker,markersize=2.7,capsize=2,label=legend)
                else:
                    ax.plot(threads,[v[0] for v in points],color=color,marker=marker,markersize=2.7,label=legend)
            ax.set_xscale('log',base=2); ax.set_xticks(threads,labels=list(map(str,threads)))
            if metric=='mean_response_us': ax.set_yscale('log')
            else: ax.set_ylim(bottom=0)
            ax.set_xlabel('Client threads'); ax.set_ylabel(label)
            ax.grid(axis='y',alpha=.2,linewidth=.5); ax.legend(frameon=False,loc='best',handlelength=1.2)
            save(fig,stem)
    labels=['Cassandra\nread-heavy\n64 threads','Cassandra\nwrite-heavy\n128 threads',
            'MongoDB\nread-heavy\n64 threads','MongoDB\nwrite-heavy\n1 thread']
    for stem,metric,div,label in [('image12','throughput_ops_s',1000,'Mean throughput (kops/s)'),
                                 ('image11','read_p99_us',1000,'Read p99 (ms; log scale)')]:
        fig,ax=plt.subplots(figsize=(3.33,2.2),layout='constrained')
        for k,size in enumerate(['base','upgraded']):
            points=[record(stem,db,w,size,t,metric,div)[0] for db,w,t in SELECTED]
            ax.bar([i+(k-.5)*.36 for i in range(4)],points,width=.34,color=COLORS[k],
                   label=['16 GB baseline','32 GB upgraded'][k],zorder=3)
        ax.set_xticks(range(4),labels=labels); ax.set_ylabel(label)
        if metric=='read_p99_us': ax.set_yscale('log'); ax.set_ylim(.1,20000)
        else: ax.set_ylim(0,65000/div)
        ax.legend(frameon=False,loc='upper left',ncol=2); ax.grid(axis='y',alpha=.2,zorder=0)
        save(fig,stem)
    fig,axes=plt.subplots(1,3,figsize=(7,2.05),layout='constrained')
    for ax,metric,title in zip(axes,['db_cpu_util_pct','db_cpu_iowait_pct','db_disk_util_pct'],
                              ['CPU utilization','CPU I/O wait','Disk utilization']):
        for k,size in enumerate(['base','upgraded']):
            vals=[record('image2',db,w,size,t,metric,1)[0] for db,w,t in SELECTED]
            ax.bar([i+(k-.5)*.36 for i in range(4)],vals,width=.34,color=COLORS[k],
                   label=['16 GB baseline','32 GB upgraded'][k],zorder=3)
        ax.set_xticks(range(4),labels=['Cas.\nread','Cas.\nwrite','Mongo\nread','Mongo\nwrite'])
        ax.set_ylim(0,110); ax.set_ylabel(title+' (%)'); ax.grid(axis='y',alpha=.2,zorder=0)
    axes[0].legend(frameon=False,ncol=2,loc='lower left',bbox_to_anchor=(0,1))
    save(fig,'image2')
    with (args.out/'plot-data.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(plotrows[0]));writer.writeheader();writer.writerows(plotrows)
    print(f'Generated 9 data figures (PDF + PNG) and {len(plotrows)} plot-data rows in {args.out}')

if __name__=='__main__': main()
