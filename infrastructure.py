import pulp
from math import ceil, floor
import yaml
from easy_struct import Struct
from collections import namedtuple


Provider = Struct('Provider', ['name', 'instances', 'max_machines', 'transfer_price_in', 'transfer_price_out', 'storages'])
Instance = Struct('Instance', ['name', 'price', 'ccu', 'provider'])
Storage = Struct('Storage', ['name', 'transfer_price_in', 'transfer_price_out'])
Infrastructure = Struct('Infrastructure', ['providers', 'storages', 'instances', 'request_price'])
Application = Struct('Application', ['total_tasks', 'exec_time' 'data_size_in', 'data_size_out'])
Problem = Struct('Problem', ['infrastructure', 'application'])
Solution = namedtuple('Solution',['status','instances', 'cost'])
InstancePlan = namedtuple('InstancePlan', ['instance', 'task_assignment', 'number_instances', 'tail_hours'])

def load_infrastructure(filename="infrastructure.yaml"):
    stream = open(filename, 'r')
    data = yaml.load(stream)
    stream.close()

    storages = {name: Storage(name=name,
                              transfer_price_in=data['transfer']['price_in'],
                              transfer_price_out=data['transfer']['price_out']
    ) for name, data in data['storage']['providers'].iteritems()
    }

    providers = {name: Provider(name=name,
                                instances={name: Instance(name=name, price=data['price'], ccu=data['ccu'], provider=None) for
                                           name, data in data['instances'].iteritems()},
                                max_machines=data['max_machines'],
                                transfer_price_in=data['transfer']['price_in'],
                                transfer_price_out=data['transfer']['price_out'],
                                storages=(data['transfer'].get('local') or [])
    ) for name, data in data['compute']['providers'].iteritems()
    }

    for p in providers.values():
        for i in p.instances.values():
            i.provider = p

    instances = {instance.name: instance for provider in providers.values() for instance in provider.instances.values()}

    return Infrastructure(storages=storages, providers=providers, instances=instances, request_price=data['queue']['request_price'])

def load_application(filename="application.yaml"):
    stream = open(filename, 'r')
    data = yaml.load(stream)
    stream.close()

    return Application(**data)

def transfer_rate_is(i,s):
    if s == 's3':
        return {'m2.4xlarge':50, 'm2.2xlarge':50, 'linux.c1.xlarge':50, 'm2.xlarge':50, 'm1.xlarge':50, 'm1.large':50, 'c1.medium':50, 'm1.small':50, 'rs-16gb':30, 'rs-2gb':30, 'rs-1gb':30, 'rs-4gb':30, 'gg-8gb':30, 'gg-4gb':30, 'gg-2gb':30, 'gg-1gb':30, 'eh-8gb-20gh':20, 'eh-4gb-8gh':20, 'eh-2gb-4gh':20, 'eh-1gb-2gh':20, 'private':15}[i]
    else: 
        return {'m2.4xlarge':20, 'm2.2xlarge':20, 'linux.c1.xlarge':20, 'm2.xlarge':20, 'm1.xlarge':20, 'm1.large':20, 'c1.medium':20, 'm1.small':20, 'rs-16gb':60, 'rs-2gb':60, 'rs-1gb':60, 'rs-4gb':60, 'gg-8gb':20, 'gg-4gb':20, 'gg-2gb':20, 'gg-1gb':20, 'eh-8gb-20gh':25, 'eh-4gb-8gh':25, 'eh-2gb-4gh':25, 'eh-1gb-2gh':25, 'private':20}[i]

def solve_probem(app, infrastructure, storage, deadline):
    s = storage
    request_price = infrastructure.request_price
    storages = infrastructure.storages
    instances = infrastructure.instances
    providers = infrastructure.providers
    total_tasks = app.total_tasks
    #data_size_in = app.data_size_in
    #data_size_out = app.data_size_out
    #exec_time = app.exec_time
    ## Problem params

    is_local = lambda i:  0.0 if s in instances[i].provider.storages else 1.0

    ccu = lambda i: instances[i].ccu
    transfer_rate = lambda i: transfer_rate_is(i,s)
    transfer_time = lambda i: (app.data_size_in + app.data_size_out) / (transfer_rate(i) * 3600.0)
    unit_time = lambda i: max(app.exec_time / ccu(i), transfer_time(i))
    transfer_cost = lambda i: (app.data_size_out * (instances[i].provider.transfer_price_out + storages[s].transfer_price_in) + app.data_size_in * (storages[s].transfer_price_out + instances[i].provider.transfer_price_in)) * is_local(i);
    instance_deadline = lambda i: ceil(floor((deadline - transfer_time(i)) / unit_time(i)) * unit_time(i))
    time_quantum = lambda i: ceil(unit_time(i))
    tasks_per_deadline = lambda i: floor((deadline - transfer_time(i)) / unit_time(i))
    tasks_per_time_quantum = lambda i: floor(time_quantum(i) / unit_time(i))



    ##### Model
    ## Problem
    problem = pulp.LpProblem(name='cloud')

    ## Variables

    TaskAssignment = {name: pulp.LpVariable('ta_%s' % instance.name, lowBound=0, upBound=total_tasks, cat='Integer') for (name, instance) in instances.iteritems()}
    NumberInstances = {name: pulp.LpVariable('ni_%s' % instance.name, lowBound=0, upBound=instance.provider.max_machines, cat='Integer') for (name, instance) in instances.iteritems()}
    TailTaskHours = {name: pulp.LpVariable('tth_%s' % instance.name, lowBound=0, upBound=deadline - 1, cat='Integer') for (name, instance) in instances.iteritems()}
    HasTail = {name: pulp.LpVariable('ht_%s' % instance.name, cat='Binary') for (name, instance) in instances.iteritems()}

    ## Objective

    problem += sum([
        (
            (instance_deadline(i) * NumberInstances[i] + TailTaskHours[i]) * instances[i].price
            + TaskAssignment[i] * (request_price + transfer_cost(i))
        ) for i in instances.keys()
    ]), 'Objective'

    for i in instances.keys():
        problem += TaskAssignment[i] >= NumberInstances[i] * tasks_per_deadline(i), 'TaskAssignmentLowerBound_' + i
        problem += TaskAssignment[i] <= NumberInstances[i] * tasks_per_deadline(i) + max(0, tasks_per_deadline(i) - 1), 'TaskAssignmentUpperBound_' + i

        problem += TailTaskHours[i] >= (TaskAssignment[i] - NumberInstances[i] * tasks_per_deadline(i)) * unit_time(i), 'TailTasksHoursLowerBound_' + i
        problem += TailTaskHours[i] <= (TaskAssignment[i] - NumberInstances[i] * tasks_per_deadline(i) + tasks_per_time_quantum(i)) * unit_time(i), 'TailTasksHoursUpperBound_' + i

        problem += TailTaskHours[i] >= HasTail[i], 'SetHasTailLowerBound_' + i
        problem += TailTaskHours[i] <= max(deadline - 1, 0) * HasTail[i], 'SetHasTailUpperBound_' + i

    for p in providers.values():
        problem += sum([HasTail[i] + NumberInstances[i] for i in p.instances.keys()]) <= p.max_machines, 'MaxMachines_' + p.name

    problem += sum(TaskAssignment.values()) == total_tasks, 'SumTasks'


    problem.solve()
    #
    #print "Status:", problem.status
    #for i in instances.keys():
    #    print "%15s: %4.0f %3.0f %3.0f %3.0f" % (
    #    i, TaskAssignment[i].value(), NumberInstances[i].value(), HasTail[i].value(), TailTaskHours[i].value())

    #print
    #print "Objective: ", problem.objective.value()
    if problem.status != pulp.LpStatusOptimal:
        return Solution(status=pulp.LpStatus[problem.status], cost=None, instances=None)
    else:
        return Solution(status=pulp.LpStatus[problem.status], cost=problem.objective.value(), instances=[InstancePlan(i, TaskAssignment[i].value(), NumberInstances[i].value(), TailTaskHours[i].value()) for i in instances.keys() if (TaskAssignment[i].value() > 1e-6) ])


infrastructure = load_infrastructure()
app = load_application()
print app.__dict__

#i = 17
#result = solve_probem(app, infrastructure, 's3', i)
#print i, result

for i in xrange(1,50):
    result = solve_probem(app, infrastructure, 's3', i)
    print i, result.cost, result
